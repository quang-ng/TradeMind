import asyncio
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape

import httpx
from common.config import DatabaseSettings, NotifierSettings
from common.db.models import AuditEvent, NotifierState, Position
from common.enums import AuditEventType, PositionStatus
from common.logging import configure_json_logging
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .email_client import EmailClient
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


def _describe_window(stats: _WindowStats, *, equity_usdt: Decimal | None) -> str:
    """Verbose, email-appropriate rendering of a `_WindowStats` window —
    deliberately more explicit than `_format_rollup_line` (kept as-is for
    the terse Telegram daily summary): spells out the win/loss split and
    what the %-of-equity figure actually means, since an email is read
    stand-alone rather than as one line in a running chat feed."""
    pct = (
        f" — that's {stats.pnl_usdt / equity_usdt:+.2%} of the current account equity"
        if equity_usdt
        else ""
    )
    if stats.trade_count == 0:
        return f"{stats.pnl_usdt:.4f} USDT (no trades closed in this window)"
    win_rate = f"{stats.win_rate_pct:.0f}%" if stats.win_rate_pct is not None else "—"
    trades_word = "trade" if stats.trade_count == 1 else "trades"
    wins_word = "win" if stats.wins == 1 else "wins"
    losses_word = "loss" if stats.losses == 1 else "losses"
    return (
        f"{stats.pnl_usdt:.4f} USDT from {stats.trade_count} closed {trades_word} "
        f"({stats.wins} {wins_word}, {stats.losses} {losses_word}, {win_rate} win rate){pct}"
    )


def _pnl_color(pnl: Decimal) -> str:
    if pnl > 0:
        return "#059669"
    if pnl < 0:
        return "#dc2626"
    return "#4b5563"


def _pnl_tint(pnl: Decimal) -> tuple[str, str]:
    """(background, border) pair matching `_pnl_color`, for the HTML stat cards."""
    if pnl > 0:
        return "#ecfdf5", "#a7f3d0"
    if pnl < 0:
        return "#fef2f2", "#fecaca"
    return "#f3f4f6", "#e5e7eb"


def _pnl_arrow(pnl: Decimal) -> str:
    if pnl > 0:
        return "▲"  # ▲
    if pnl < 0:
        return "▼"  # ▼
    return "–"  # –


def _html_stat_card(
    title: str,
    subtitle: str,
    stats: _WindowStats,
    *,
    equity_usdt: Decimal | None,
    large: bool,
) -> str:
    """One stat tile (This week / Previous week / Since live). `subtitle`
    spells out in plain English what the window actually covers — the
    thing a Telegram line has no room for but an email does."""
    color = _pnl_color(stats.pnl_usdt)
    bg, border = _pnl_tint(stats.pnl_usdt)
    arrow = _pnl_arrow(stats.pnl_usdt)
    pct = (
        f"&nbsp;&nbsp;&middot;&nbsp;&nbsp;{stats.pnl_usdt / equity_usdt:+.2%} of current equity"
        if equity_usdt
        else ""
    )
    if stats.trade_count == 0:
        detail = "No trades closed in this window."
    else:
        win_rate = f"{stats.win_rate_pct:.0f}%" if stats.win_rate_pct is not None else "&mdash;"
        detail = (
            f"{stats.trade_count} trade{'s' if stats.trade_count != 1 else ''} closed "
            f"&mdash; {stats.wins}W / {stats.losses}L ({win_rate} win rate)"
        )
    number_size = "26px" if large else "19px"
    padding = "18px 20px" if large else "14px 16px"
    return f"""<div style="background:{bg};border:1px solid {border};border-radius:10px;padding:{padding};">
<div style="font-size:12px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;color:#6b7280;">{escape(title)}</div>
<div style="font-size:11px;color:#9ca3af;margin-top:2px;">{escape(subtitle)}</div>
<div style="font-size:{number_size};font-weight:700;color:{color};font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace;margin-top:8px;">{arrow}&nbsp;{stats.pnl_usdt:.4f} USDT</div>
<div style="font-size:12px;color:#6b7280;margin-top:4px;">{detail}{pct}</div>
</div>"""


def _html_trade_row(p: Position) -> str:
    pnl = p.pnl_usdt if p.pnl_usdt is not None else Decimal("0")
    color = _pnl_color(pnl)
    pct = f"{p.pnl_pct:+.2%}" if p.pnl_pct is not None else "&mdash;"
    entry = p.entry_price if p.entry_price is not None else "&mdash;"
    exit_price = p.exit_price if p.exit_price is not None else "&mdash;"
    cell = "padding:10px 12px;border-bottom:1px solid #f0f1f3;font-size:13px;"
    return f"""<tr>
<td style="{cell}color:#111827;font-weight:600;">{escape(p.symbol)}</td>
<td style="{cell}color:#6b7280;font-family:'SFMono-Regular',Consolas,monospace;font-size:12px;white-space:nowrap;">{entry}&nbsp;&rarr;&nbsp;{exit_price}</td>
<td style="{cell}color:{color};font-weight:600;text-align:right;font-family:'SFMono-Regular',Consolas,monospace;">{pnl:.4f}</td>
<td style="{cell}color:{color};text-align:right;font-family:'SFMono-Regular',Consolas,monospace;">{pct}</td>
</tr>"""


def _render_weekly_html(
    *,
    window_start: datetime,
    window_end: datetime,
    week_stats: _WindowStats,
    week_positions: Sequence[Position],
    prev_week_stats: _WindowStats,
    live_stats: _WindowStats,
    live_start: datetime,
    equity_usdt: Decimal | None,
    status: dict | None,
) -> str:
    """Email-only HTML rendering of the same weekly rollup as the plain-text
    body. Inline CSS + `<table>` layout, no external stylesheet or JS, so it
    renders consistently across email clients — that constraint is why this
    can't reuse the web/artifact CSS conventions used elsewhere in the repo.
    Every dynamic *string* (trading-pair symbols) is HTML-escaped; every
    other interpolated value is our own Decimal/int/date, never raw text."""
    if week_positions:
        rows = "".join(
            _html_trade_row(p)
            for p in sorted(week_positions, key=lambda p: p.closed_at or window_start)
        )
        head_cell = (
            "padding:0 12px 8px;font-size:11px;font-weight:600;text-transform:uppercase;"
            "letter-spacing:.03em;color:#9ca3af;border-bottom:1px solid #e5e7eb;"
        )
        trades_section = f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-top:10px;">
<thead><tr>
<th align="left" style="{head_cell}">Pair</th>
<th align="left" style="{head_cell}">Entry &rarr; exit price</th>
<th align="right" style="{head_cell}">P&amp;L (USDT)</th>
<th align="right" style="{head_cell}">P&amp;L (%)</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>"""
    else:
        trades_section = (
            '<p style="font-size:13px;color:#9ca3af;margin:10px 0 0;">'
            "No trades closed this week.</p>"
        )

    killswitch_row = ""
    if status is not None and status.get("killswitch_enabled"):
        killswitch_row = """<tr><td style="padding:16px 28px 0;">
<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px 16px;font-size:13px;color:#991b1b;font-weight:600;">
&#9888;&nbsp; Kill switch is currently ON &mdash; no new trades will be opened until it is turned off.
</div>
</td></tr>"""

    account_row = ""
    if equity_usdt is not None and status is not None:
        account_row = f"""<tr><td style="padding:20px 28px 0;">
<div style="font-size:12px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;color:#6b7280;margin-bottom:8px;">Account snapshot (right now)</div>
<div style="font-size:13px;color:#374151;line-height:1.7;">
Current equity: <strong>{equity_usdt:.2f} USDT</strong><br>
Open positions: <strong>{status["open_positions"]}</strong>
</div>
</td></tr>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>TradeMind Weekly Summary</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;">
<tr><td style="padding:24px 28px 0;">
<div style="font-size:13px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#4f46e5;">TradeMind</div>
<div style="font-size:19px;font-weight:700;color:#111827;margin-top:4px;">Weekly Performance Summary</div>
<div style="font-size:13px;color:#6b7280;margin-top:2px;">{window_start.date().isoformat()} &rarr; {window_end.date().isoformat()} (UTC)</div>
</td></tr>
<tr><td style="padding:16px 28px 0;">
<div style="font-size:12px;color:#9ca3af;line-height:1.6;border-top:1px solid #f0f1f3;padding-top:14px;">
All figures below are <strong>realized</strong> profit/loss &mdash; from trades that already closed
in each window. Unrealized P&amp;L on positions still open is not included.
</div>
</td></tr>
<tr><td style="padding:16px 28px 0;">
{_html_stat_card("This week", "Realized P&L, last 7 days", week_stats, equity_usdt=equity_usdt, large=True)}
</td></tr>
<tr><td style="padding:14px 28px 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td width="50%" style="padding-right:6px;">
{_html_stat_card("Previous week", "The 7 days before that", prev_week_stats, equity_usdt=equity_usdt, large=False)}
</td>
<td width="50%" style="padding-left:6px;">
{_html_stat_card("Since live", f"Cumulative since {live_start.date().isoformat()}", live_stats, equity_usdt=equity_usdt, large=False)}
</td>
</tr></table>
</td></tr>
<tr><td style="padding:20px 28px 0;">
<div style="font-size:12px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;color:#6b7280;">Trades closed this week</div>
{trades_section}
</td></tr>
{killswitch_row}
{account_row}
<tr><td style="padding:20px 28px;">
<div style="font-size:11px;color:#9ca3af;border-top:1px solid #f0f1f3;padding-top:14px;">Automated weekly report from TradeMind's notifier service.</div>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


async def _send_weekly_pnl_summary(
    session_factory: async_sessionmaker[AsyncSession],
    email: EmailClient,
    settings: NotifierSettings,
    window_end: datetime,
) -> None:
    """Weekly companion to `_send_daily_pnl_summary`, sent by email instead
    of Telegram. Rolls up the trailing 7d (the week just finished) against
    the 7d before that for a week-over-week trend, plus the same
    since-go-live cumulative context the daily summary already computes —
    same rationale: a single week's realized PnL still isn't much signal at
    this trade frequency without something to compare it against.

    Unlike the terse Telegram daily summary, this is read stand-alone in an
    inbox, so both the plain-text body and the HTML alternative spell out
    what each number means (win/loss counts, %-of-equity, which window is
    which) instead of packing it into one compact line."""
    window_start = window_end - timedelta(days=7)
    prev_window_start = window_start - timedelta(days=7)
    live_start = datetime.fromisoformat(settings.live_trading_started_at)

    async with session_factory() as session:
        week_positions = await _closed_positions_since(session, start=window_start, end=window_end)
        prev_week_positions = await _closed_positions_since(
            session, start=prev_window_start, end=window_start
        )
        live_positions = await _closed_positions_since(session, start=live_start, end=window_end)

    week_stats = _window_stats(week_positions)
    prev_week_stats = _window_stats(prev_week_positions)
    live_stats = _window_stats(live_positions)

    status = await _fetch_status(settings)
    equity_usdt = Decimal(str(status["equity_usdt"])) if status else None

    lines = [
        "TradeMind - Weekly Performance Summary",
        f"{window_start.date().isoformat()} -> {window_end.date().isoformat()} (UTC)",
        "",
        "All figures below are realized profit/loss from trades that closed in each",
        "window below - unrealized P&L on positions still open is not included.",
        "",
        f"This week (last 7 days): {_describe_window(week_stats, equity_usdt=equity_usdt)}",
    ]
    lines.extend(
        _format_trade_line(p)
        for p in sorted(week_positions, key=lambda p: p.closed_at or window_start)
    )
    lines += [
        "",
        "Previous week (the 7 days before that): "
        f"{_describe_window(prev_week_stats, equity_usdt=equity_usdt)}",
        f"Since live trading began ({live_start.date().isoformat()}): "
        f"{_describe_window(live_stats, equity_usdt=equity_usdt)}",
    ]
    if status is not None:
        lines += [
            "",
            f"Current equity: {equity_usdt:.2f} USDT",
            f"Open positions right now: {status['open_positions']}",
        ]
        if status.get("killswitch_enabled"):
            lines += [
                "",
                "WARNING: Kill switch is currently ON - no new trades will be opened "
                "until it is turned off.",
            ]

    html_body = _render_weekly_html(
        window_start=window_start,
        window_end=window_end,
        week_stats=week_stats,
        week_positions=week_positions,
        prev_week_stats=prev_week_stats,
        live_stats=live_stats,
        live_start=live_start,
        equity_usdt=equity_usdt,
        status=status,
    )

    subject = (
        f"TradeMind Weekly Summary - {window_start.date().isoformat()} "
        f"to {window_end.date().isoformat()}"
    )
    await email.send_email(subject, "\n".join(lines), html_body)


def _next_weekly_run(now: datetime, *, weekday: int, hour: int) -> datetime:
    """Next UTC instant matching `weekday` (0=Monday..6=Sunday, `date.weekday()`
    convention) and `hour`, strictly after `now`. Pure so the weekday-rollover
    math (today-is-the-day-but-already-past-the-hour, etc.) is unit-testable
    without driving a real `asyncio.sleep`."""
    next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    days_ahead = (weekday - next_run.weekday()) % 7
    next_run += timedelta(days=days_ahead)
    if next_run <= now:
        next_run += timedelta(days=7)
    return next_run


async def _weekly_pnl_summary_loop(
    session_factory: async_sessionmaker[AsyncSession],
    email: EmailClient,
    settings: NotifierSettings,
) -> None:
    """Sends one weekly performance email per week, at
    `settings.weekly_pnl_report_weekday_utc`/`weekly_pnl_report_hour_utc`
    (UTC) — defaults to Monday 08:00 UTC. Same sleep-until-next-run pattern
    as `_daily_pnl_summary_loop`, via `_next_weekly_run` for the weekday
    math."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            next_run = _next_weekly_run(
                now,
                weekday=settings.weekly_pnl_report_weekday_utc,
                hour=settings.weekly_pnl_report_hour_utc,
            )
            await asyncio.sleep((next_run - now).total_seconds())
            await _send_weekly_pnl_summary(session_factory, email, settings, next_run)
        except Exception:
            logger.exception("weekly_pnl_summary_failed")
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
    email = EmailClient(settings)

    logger.info("notifier_started")
    try:
        await asyncio.gather(
            _poll_audit_events(session_factory, telegram, settings.audit_poll_interval_seconds),
            _poll_telegram_commands(
                session_factory, telegram, settings, settings.telegram_poll_interval_seconds
            ),
            _daily_pnl_summary_loop(session_factory, telegram, settings),
            _weekly_pnl_summary_loop(session_factory, email, settings),
        )
    finally:
        await telegram.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_notifier())
