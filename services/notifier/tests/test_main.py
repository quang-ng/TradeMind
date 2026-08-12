import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from common.config import NotifierSettings
from common.db.models import Order, Position, RiskDecision, Signal
from common.enums import Action, OrderStatus, PositionStatus, SignalStatus
from notifier.app.main import (
    _fetch_status,
    _format_rollup_line,
    _format_trade_line,
    _send_daily_pnl_summary,
    _window_stats,
    _WindowStats,
)

NOW = datetime(2026, 8, 11, 0, 0, tzinfo=timezone.utc)


# --- pure formatting/aggregation logic (no DB, no network) --------------


def test_window_stats_empty():
    stats = _window_stats([])
    assert stats == _WindowStats(pnl_usdt=Decimal("0"), wins=0, losses=0)
    assert stats.trade_count == 0
    assert stats.win_rate_pct is None


def test_window_stats_counts_wins_and_losses_and_sums_pnl():
    positions = [
        Position(symbol="BTC/USDT", pnl_usdt=Decimal("5.5")),
        Position(symbol="ETH/USDT", pnl_usdt=Decimal("-2.0")),
        Position(symbol="SOL/USDT", pnl_usdt=Decimal("-1.0")),
    ]
    stats = _window_stats(positions)
    assert stats.pnl_usdt == Decimal("2.5")
    assert stats.wins == 1
    assert stats.losses == 2
    assert stats.trade_count == 3
    assert stats.win_rate_pct == Decimal(1) / Decimal(3) * 100


def test_format_rollup_line_includes_pct_of_equity_when_equity_known():
    stats = _WindowStats(pnl_usdt=Decimal("-0.22"), wins=0, losses=1)
    line = _format_rollup_line("Today", stats, equity_usdt=Decimal("114.78"))
    assert line == "Today: -0.2200 USDT (-0.19%) | 1 trades, 0% win rate"


def test_format_rollup_line_omits_pct_when_equity_unknown():
    stats = _WindowStats(pnl_usdt=Decimal("-0.22"), wins=0, losses=1)
    line = _format_rollup_line("Today", stats, equity_usdt=None)
    assert "%" not in line.split("|")[0]
    assert line.startswith("Today: -0.2200 USDT | 1 trades")


def test_format_rollup_line_zero_equity_does_not_divide_by_zero():
    stats = _WindowStats(pnl_usdt=Decimal("1"), wins=1, losses=0)
    line = _format_rollup_line("Today", stats, equity_usdt=Decimal("0"))
    assert "%" not in line.split("|")[0]


def test_format_rollup_line_omits_win_rate_when_no_trades():
    line = _format_rollup_line("Today", _WindowStats(Decimal("0"), 0, 0), equity_usdt=None)
    assert "win rate" not in line
    assert line == "Today: 0.0000 USDT | 0 trades"


def test_format_trade_line():
    position = Position(
        symbol="SOL/USDT",
        entry_price=Decimal("73.68"),
        exit_price=Decimal("72.95"),
        pnl_usdt=Decimal("-0.22"),
        pnl_pct=Decimal("-0.0099"),
    )
    assert (
        _format_trade_line(position) == "  SOL/USDT: 73.68 -> 72.95, -0.2200 USDT (-0.99%)"
    )


# --- integration: real Postgres, faked Telegram + admin_api /status -----


async def _make_closed_position(
    session, *, symbol: str, pnl_usdt: Decimal, pnl_pct: Decimal, closed_at: datetime
) -> None:
    trace_id = uuid.uuid4()
    signal_id = uuid.uuid4()
    session.add(
        Signal(
            id=signal_id,
            trace_id=trace_id,
            symbol=symbol,
            timeframe="1h",
            candle_ts=closed_at,
            action=Action.BUY.value,
            confidence=Decimal("0.8"),
            reasoning="test",
            model_name="test:model",
            price=Decimal("100"),
            atr_14=Decimal("1"),
            status=SignalStatus.CONSUMED.value,
        )
    )
    await session.flush()
    decision = RiskDecision(
        trace_id=trace_id,
        signal_id=signal_id,
        approved=True,
        position_size_usdt=Decimal("100"),
        position_size_base=Decimal("1"),
        stop_loss_price=Decimal("95"),
        equity_snapshot_usdt=Decimal("100"),
        risk_pct_applied=Decimal("0.01"),
    )
    session.add(decision)
    await session.flush()
    order = Order(
        trace_id=trace_id,
        risk_decision_id=decision.id,
        symbol=symbol,
        side="BUY",
        status=OrderStatus.FILLED.value,
        requested_amount=Decimal("1"),
        filled_amount=Decimal("1"),
        avg_price=Decimal("100"),
        dry_run=False,
    )
    session.add(order)
    await session.flush()
    session.add(
        Position(
            symbol=symbol,
            status=PositionStatus.CLOSED.value,
            entry_order_id=order.id,
            entry_price=Decimal("100"),
            exit_price=Decimal("100") + pnl_usdt,
            amount=Decimal("1"),
            pnl_usdt=pnl_usdt,
            pnl_pct=pnl_pct,
            closed_at=closed_at,
        )
    )


class _FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, text: str) -> bool:
        self.sent.append(text)
        return True


async def test_daily_summary_rolls_up_today_week_and_since_live_separately(
    db_session_factory, monkeypatch
):
    live_start = NOW - timedelta(days=30)
    async with db_session_factory() as session:
        # Today: one loss.
        await _make_closed_position(
            session,
            symbol="SOL/USDT",
            pnl_usdt=Decimal("-0.22"),
            pnl_pct=Decimal("-0.0099"),
            closed_at=NOW - timedelta(hours=2),
        )
        # Earlier this week, still since live: one win.
        await _make_closed_position(
            session,
            symbol="ETH/USDT",
            pnl_usdt=Decimal("3.0"),
            pnl_pct=Decimal("0.01"),
            closed_at=NOW - timedelta(days=3),
        )
        # Since live but outside the 7d window: one win.
        await _make_closed_position(
            session,
            symbol="BTC/USDT",
            pnl_usdt=Decimal("1.0"),
            pnl_pct=Decimal("0.005"),
            closed_at=NOW - timedelta(days=20),
        )
        # Before go-live — must be excluded from every rollup, not just
        # "since live", since this table can hold pre-live dry-run rows.
        await _make_closed_position(
            session,
            symbol="XRP/USDT",
            pnl_usdt=Decimal("999"),
            pnl_pct=Decimal("1.0"),
            closed_at=live_start - timedelta(days=1),
        )
        await session.commit()

    telegram = _FakeTelegram()
    settings = NotifierSettings(live_trading_started_at=live_start.isoformat())
    monkeypatch.setattr(
        "notifier.app.main._fetch_status",
        lambda _settings: _fake_status(),
    )

    await _send_daily_pnl_summary(db_session_factory, telegram, settings, NOW)

    assert len(telegram.sent) == 1
    text = telegram.sent[0]
    assert "Today: -0.2200 USDT" in text
    assert "SOL/USDT: 100" in text
    assert "ETH/USDT" not in text.split("Last 7d")[0]  # not in the today section
    assert "Last 7d: 2.7800 USDT" in text  # -0.22 + 3.0
    assert "Since live (" in text
    assert "3.7800 USDT" in text  # -0.22 + 3.0 + 1.0, XRP's 999 excluded
    assert "999" not in text
    assert "Equity: 114.78 USDT | Open positions: 2" in text


async def _fake_status():
    return {"equity_usdt": "114.78", "open_positions": 2, "killswitch_enabled": False}


async def test_daily_summary_still_sends_when_status_unavailable(db_session_factory, monkeypatch):
    telegram = _FakeTelegram()
    settings = NotifierSettings(live_trading_started_at=(NOW - timedelta(days=1)).isoformat())
    monkeypatch.setattr(
        "notifier.app.main._fetch_status", lambda _settings: _none_status()
    )

    await _send_daily_pnl_summary(db_session_factory, telegram, settings, NOW)

    assert len(telegram.sent) == 1
    text = telegram.sent[0]
    assert "Today: 0.0000 USDT | 0 trades" in text
    assert "Equity:" not in text


async def _none_status():
    return None


async def test_fetch_status_returns_none_on_http_error():
    settings = NotifierSettings(admin_api_url="http://127.0.0.1:1", admin_api_key="x")
    assert await _fetch_status(settings) is None
