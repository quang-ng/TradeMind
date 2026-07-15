import asyncio
import logging
import uuid
from datetime import datetime, timezone

import httpx
from common.config import DatabaseSettings, NotifierSettings
from common.db.models import AuditEvent, NotifierState
from common.enums import AuditEventType
from common.logging import configure_json_logging
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .telegram_client import TelegramClient

configure_json_logging()
logger = logging.getLogger(__name__)

_NIL_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")
_AUDIT_BATCH_SIZE = 50


def _format_event(event: AuditEvent) -> str:
    """One human-readable line per audit event (PROJECT.md Section 7.5),
    covering every `AuditEventType` so nothing goes silently unnotified
    (Section 13: "Telegram receives a message for every signal, every risk
    decision..., every order state change, and every kill-switch
    transition"). Falls back to a generic dump for any event_type not
    explicitly handled below, rather than skipping it."""
    p = event.payload or {}
    et = event.event_type
    if et == AuditEventType.SIGNAL_RECEIVED.value:
        text = f"Signal: {p.get('symbol')} -> {p.get('action')} (confidence {p.get('confidence')})"
    elif et == AuditEventType.SIGNAL_VALIDATION_FAILED.value:
        text = f"Signal validation failed for {p.get('symbol')}: {p.get('reason')}"
    elif et == AuditEventType.RISK_APPROVED.value:
        text = f"Risk decision: APPROVED (signal {p.get('signal_id')})"
    elif et == AuditEventType.RISK_REJECTED.value:
        text = (
            f"Risk decision: REJECTED (signal {p.get('signal_id')}) - "
            f"{p.get('rejection_reason')}"
        )
    elif et == AuditEventType.ORDER_SUBMITTED.value:
        text = f"Order submitted: {p.get('symbol')} {p.get('side')} ({p.get('status')})"
    elif et == AuditEventType.ORDER_FILLED.value:
        text = f"Order filled: {p.get('pair') or p.get('symbol')} {p.get('side')}"
    elif et == AuditEventType.ORDER_FAILED.value:
        text = f"Order FAILED: {p.get('symbol')} {p.get('side')}"
    elif et == AuditEventType.ORDER_CANCELLED.value:
        text = f"Order cancelled: {p.get('symbol') or p.get('pair')} {p.get('side')}"
    elif et == AuditEventType.POSITION_OPENED.value:
        text = (
            f"Position opened: {p.get('pair')} @ {p.get('entry_price')} "
            f"(amount {p.get('amount')})"
        )
    elif et == AuditEventType.POSITION_CLOSED.value:
        text = f"Position closed: {p.get('pair')} pnl_usdt={p.get('pnl_usdt')}"
    elif et == AuditEventType.KILLSWITCH_ENABLED.value:
        text = f"KILL SWITCH ENABLED by {p.get('updated_by')}: {p.get('reason')}"
    elif et == AuditEventType.KILLSWITCH_DISABLED.value:
        text = f"Kill switch disabled by {p.get('updated_by')}: {p.get('reason')}"
    elif et == AuditEventType.CONFIG_CHANGED.value:
        text = f"Risk config changed: {p.get('changes')}"
    elif et == AuditEventType.RECONCILIATION_REQUIRED.value:
        text = (
            f"OPERATOR ACTION REQUIRED: stale order {p.get('order_id')} "
            f"for {p.get('symbol')} could not be reconciled ({p.get('reason')})"
        )
    else:
        text = f"{et}: {p}"
    return f"TradeMind | {et}\n{text}\ntrace_id={event.trace_id}"


async def _get_or_init_state(session: AsyncSession) -> NotifierState:
    state = await session.get(NotifierState, 1)
    if state is None:
        # A fresh notifier starts from "now" (PROJECT.md Section 7.8) — it
        # must never replay pre-existing audit history into Telegram.
        state = NotifierState(
            id=1, last_audit_created_at=datetime.now(timezone.utc), last_audit_id=_NIL_UUID
        )
        session.add(state)
        await session.commit()
    return state


async def _poll_audit_events(
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    interval_seconds: float,
) -> None:
    while True:
        try:
            async with session_factory() as session:
                state = await _get_or_init_state(session)
                events = (
                    await session.execute(
                        select(AuditEvent)
                        .where(
                            tuple_(AuditEvent.created_at, AuditEvent.id)
                            > (state.last_audit_created_at, state.last_audit_id)
                        )
                        .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
                        .limit(_AUDIT_BATCH_SIZE)
                    )
                ).scalars().all()

                for event in events:
                    sent = await telegram.send_message(_format_event(event))
                    if not sent:
                        # Stop this batch without advancing past the failed
                        # event, so it (and anything after it) is retried
                        # once Telegram recovers, instead of being skipped
                        # (PROJECT.md Section 9.4: notification is
                        # best-effort, but never silently dropped).
                        logger.warning(
                            "telegram_notify_deferred", extra={"event_id": str(event.id)}
                        )
                        break
                    state.last_audit_created_at = event.created_at
                    state.last_audit_id = event.id

                await session.commit()
        except Exception:
            logger.exception("audit_event_poll_failed")
        await asyncio.sleep(interval_seconds)


async def _handle_telegram_update(
    update: dict,
    telegram: TelegramClient,
    admin_http_client: httpx.AsyncClient,
    settings: NotifierSettings,
) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    text = (message.get("text") or "").strip()

    if not settings.telegram_chat_id or chat_id != settings.telegram_chat_id:
        # Anyone can message a public bot token; only the configured
        # operator chat may issue control commands (PROJECT.md Section 11:
        # Telegram is a client of the API, authenticated by chat identity).
        logger.warning("telegram_command_from_unauthorized_chat", extra={"chat_id": chat_id})
        return

    command = text.split()[0] if text else ""
    if command == "/killswitch_on":
        path = "/killswitch/enable"
    elif command == "/killswitch_off":
        path = "/killswitch/disable"
    else:
        return

    try:
        response = await admin_http_client.post(
            path, json={"reason": "telegram command", "updated_by": f"telegram:{chat_id}"}
        )
        response.raise_for_status()
        await telegram.send_message(f"OK: {command} -> {response.json()}")
    except httpx.HTTPError as exc:
        logger.error("killswitch_command_failed", extra={"command": command, "error": str(exc)})
        await telegram.send_message(f"Failed to execute {command}: {exc}")


async def _poll_telegram_commands(
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    settings: NotifierSettings,
    interval_seconds: float,
) -> None:
    """PROJECT.md Section 11: `/killswitch_on` and `/killswitch_off` call
    the admin API internally — "Telegram is a client of the API, not a
    parallel control path" — never touches Postgres/Redis kill-switch state
    directly."""
    admin_http_client = httpx.AsyncClient(
        base_url=settings.admin_api_url,
        headers={"Authorization": f"Bearer {settings.admin_api_key}"},
        timeout=15.0,
    )
    try:
        while True:
            try:
                async with session_factory() as session:
                    state = await _get_or_init_state(session)
                    offset = (
                        state.last_telegram_update_id + 1
                        if state.last_telegram_update_id is not None
                        else None
                    )
                    updates = await telegram.get_updates(offset=offset)
                    for update in updates:
                        await _handle_telegram_update(update, telegram, admin_http_client, settings)
                        state.last_telegram_update_id = update["update_id"]
                    if updates:
                        await session.commit()
                    else:
                        await session.rollback()
            except Exception:
                logger.exception("telegram_command_poll_failed")
            await asyncio.sleep(interval_seconds)
    finally:
        await admin_http_client.aclose()


async def run_notifier() -> None:
    settings = NotifierSettings()
    engine = create_async_engine(DatabaseSettings().postgres_dsn)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    telegram = TelegramClient(settings)

    logger.info("notifier_started")
    try:
        await asyncio.gather(
            _poll_audit_events(session_factory, telegram, settings.audit_poll_interval_seconds),
            _poll_telegram_commands(
                session_factory, telegram, settings, settings.telegram_poll_interval_seconds
            ),
        )
    finally:
        await telegram.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_notifier())
