import asyncio
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
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


@dataclass
class _WindowStats:
    pnl_usdt: Decimal
    wins: int
    losses: int

    @property
    def trade_count(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate_pct(self) -> Decimal | None:
        return (Decimal(self.wins) / self.trade_count * 100) if self.trade_count else None


def _window_stats(positions: Sequence[Position]) -> _WindowStats:
    pnl = sum((p.pnl_usdt or Decimal("0") for p in positions), start=Decimal("0"))
    wins = sum(1 for p in positions if (p.pnl_usdt or Decimal("0")) > 0)
    losses = sum(1 for p in positions if (p.pnl_usdt or Decimal("0")) < 0)
    return _WindowStats(pnl_usdt=pnl, wins=wins, losses=losses)


def _format_rollup_line(label: str, stats: _WindowStats, *, equity_usdt: Decimal | None) -> str:
    # equity_usdt is the *current* balance, not what it was over this
    # window, so this is a rough "PnL relative to where the account
    # stands now" — same approximation admin_api's /status daily_pnl_pct
    # already makes, not a time-weighted return.
    pct = f" ({stats.pnl_usdt / equity_usdt:.2%})" if equity_usdt else ""
    win_rate = f", {stats.win_rate_pct:.0f}% win rate" if stats.win_rate_pct is not None else ""
    return f"{label}: {stats.pnl_usdt:.4f} USDT{pct} | {stats.trade_count} trades{win_rate}"


def _format_trade_line(position: Position) -> str:
    pct = f" ({position.pnl_pct:.2%})" if position.pnl_pct is not None else ""
    pnl = position.pnl_usdt if position.pnl_usdt is not None else Decimal("0")
    return (
        f"  {position.symbol}: {position.entry_price} -> {position.exit_price}, "
        f"{pnl:.4f} USDT{pct}"
    )


async def _fetch_status(settings: NotifierSettings) -> dict | None:
    """Best-effort `GET /status` for equity/open-position context. The
    summary must still send with just the PnL rollups below if admin_api
    or the balance snapshot is unavailable (Section 11: equity is never
    synthesized, so /status legitimately 503s sometimes)."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.admin_api_url,
            headers={"Authorization": f"Bearer {settings.admin_api_key}"},
            timeout=15.0,
        ) as client:
            response = await client.get("/status")
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError:
        logger.warning("daily_pnl_summary_status_unavailable", exc_info=True)
        return None


async def _closed_positions_since(
    session: AsyncSession, *, start: datetime, end: datetime
) -> list[Position]:
    return list(
        (
            await session.execute(
                select(Position).where(
                    Position.status == PositionStatus.CLOSED.value,
                    Position.closed_at >= start,
                    Position.closed_at < end,
                )
            )
        )
        .scalars()
        .all()
    )


async def _send_daily_pnl_summary(
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    settings: NotifierSettings,
    window_end: datetime,
) -> None:
    """A single day's realized PnL is noisy at this trade frequency (often
    0-2 closed trades/day), so alongside that day's rollup this also sends
    a trailing-7d and a since-go-live cumulative rollup for context, plus
    current equity/open-position count when admin_api's balance snapshot
    is fresh (2026-08-11 — see NotifierSettings.live_trading_started_at)."""
    window_start = window_end - timedelta(days=1)
    week_start = window_end - timedelta(days=7)
    live_start = datetime.fromisoformat(settings.live_trading_started_at)

    async with session_factory() as session:
        today_positions = await _closed_positions_since(session, start=window_start, end=window_end)
        week_positions = await _closed_positions_since(session, start=week_start, end=window_end)
        live_positions = await _closed_positions_since(session, start=live_start, end=window_end)

    status = await _fetch_status(settings)
    # Decimal("0") is falsy, so _format_rollup_line's `if equity_usdt` below
    # correctly skips the %-of-equity suffix instead of dividing by zero.
    equity_usdt = Decimal(str(status["equity_usdt"])) if status else None

    lines = [
        "TradeMind | Daily PnL",
        f"{window_start.date().isoformat()} -> {window_end.date().isoformat()} (UTC)",
        "",
        _format_rollup_line("Today", _window_stats(today_positions), equity_usdt=equity_usdt),
    ]
    lines.extend(
        _format_trade_line(p)
        for p in sorted(today_positions, key=lambda p: p.closed_at or window_start)
    )
    lines += [
        "",
        _format_rollup_line("Last 7d", _window_stats(week_positions), equity_usdt=equity_usdt),
        _format_rollup_line(
            f"Since live ({live_start.date().isoformat()})",
            _window_stats(live_positions),
            equity_usdt=equity_usdt,
        ),
    ]
    if status is not None:
        killswitch_note = " | KILL SWITCH ON" if status.get("killswitch_enabled") else ""
        lines += [
            "",
            f"Equity: {equity_usdt:.2f} USDT | Open positions: {status['open_positions']}"
            f"{killswitch_note}",
        ]

    await telegram.send_message("\n".join(lines))


async def _daily_pnl_summary_loop(
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    settings: NotifierSettings,
) -> None:
    """Sends one realized-PnL rollup per day at `settings.daily_pnl_report_hour_utc`
    (UTC), covering the trailing 24h window rather than the calendar day so
    it reads correctly no matter which hour is configured."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            next_run = now.replace(
                hour=settings.daily_pnl_report_hour_utc, minute=0, second=0, microsecond=0
            )
            if next_run <= now:
                next_run += timedelta(days=1)
            await asyncio.sleep((next_run - now).total_seconds())
            await _send_daily_pnl_summary(session_factory, telegram, settings, next_run)
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
            _daily_pnl_summary_loop(session_factory, telegram, settings),
        )
    finally:
        await telegram.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_notifier())
