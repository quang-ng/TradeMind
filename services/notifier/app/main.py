import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from common.config import DatabaseSettings, NotifierSettings
from common.db.models import AuditEvent, NotifierState, Position
from common.enums import AuditEventType, PositionStatus
from common.logging import configure_json_logging
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .telegram_client import TelegramClient

configure_json_logging()
logger = logging.getLogger(__name__)

_NIL_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")
_AUDIT_BATCH_SIZE = 50

# Telegram is now limited to buy/sell trade actions plus the safety alerts an
# operator must act on (kill-switch transitions, unreconciled orders) — every
# other audit event (signals, risk decisions, order submitted/failed/etc.)
# stays in the audit log but is no longer relayed.
_NOTIFY_EVENT_TYPES = frozenset(
    {
        AuditEventType.POSITION_OPENED.value,
        AuditEventType.POSITION_CLOSED.value,
        AuditEventType.KILLSWITCH_ENABLED.value,
        AuditEventType.KILLSWITCH_DISABLED.value,
        AuditEventType.RECONCILIATION_REQUIRED.value,
    }
)


def _format_event(event: AuditEvent) -> str:
    """One human-readable line for each notifiable audit event type
    (buy/sell + safety alerts, see `_NOTIFY_EVENT_TYPES`)."""
    p = event.payload or {}
    et = event.event_type
    if et == AuditEventType.POSITION_OPENED.value:
        text = (
            f"BUY: {p.get('pair')} @ {p.get('entry_price')} "
            f"(amount {p.get('amount')})"
        )
    elif et == AuditEventType.POSITION_CLOSED.value:
        text = f"SELL: {p.get('pair')} pnl_usdt={p.get('pnl_usdt')}"
    elif et == AuditEventType.KILLSWITCH_ENABLED.value:
        text = f"KILL SWITCH ENABLED by {p.get('updated_by')}: {p.get('reason')}"
    elif et == AuditEventType.KILLSWITCH_DISABLED.value:
        text = f"Kill switch disabled by {p.get('updated_by')}: {p.get('reason')}"
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
                    if event.event_type in _NOTIFY_EVENT_TYPES:
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


async def _send_daily_pnl_summary(
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    window_end: datetime,
) -> None:
    window_start = window_end - timedelta(days=1)
    async with session_factory() as session:
        closed_positions = (
            await session.execute(
                select(Position).where(
                    Position.status == PositionStatus.CLOSED.value,
                    Position.closed_at >= window_start,
                    Position.closed_at < window_end,
                )
            )
        ).scalars().all()

    total_pnl = sum((p.pnl_usdt or Decimal("0") for p in closed_positions), start=Decimal("0"))
    wins = sum(1 for p in closed_positions if (p.pnl_usdt or Decimal("0")) > 0)
    losses = sum(1 for p in closed_positions if (p.pnl_usdt or Decimal("0")) < 0)
    text = (
        f"TradeMind | Daily PnL\n"
        f"{window_start.date().isoformat()} -> {window_end.date().isoformat()} (UTC)\n"
        f"Realized PnL: {total_pnl} USDT\n"
        f"Closed trades: {len(closed_positions)} (wins {wins} / losses {losses})"
    )
    await telegram.send_message(text)


async def _daily_pnl_summary_loop(
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    report_hour_utc: int,
) -> None:
    """Sends one realized-PnL rollup per day at `report_hour_utc` (UTC),
    covering the trailing 24h window rather than the calendar day so it
    reads correctly no matter which hour is configured."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            next_run = now.replace(hour=report_hour_utc, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            await asyncio.sleep((next_run - now).total_seconds())
            await _send_daily_pnl_summary(session_factory, telegram, next_run)
        except Exception:
            logger.exception("daily_pnl_summary_failed")
            await asyncio.sleep(60.0)


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
            _daily_pnl_summary_loop(session_factory, telegram, settings.daily_pnl_report_hour_utc),
        )
    finally:
        await telegram.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_notifier())
