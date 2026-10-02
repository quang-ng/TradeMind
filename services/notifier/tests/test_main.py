import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from common.config import NotifierSettings
from common.db.models import AuditEvent, Order, Position, RiskDecision, Signal
from common.enums import Action, AuditEventType, OrderStatus, PositionStatus, SignalStatus

from notifier.app.main import (
    _NOTIFY_EVENT_TYPES,
    _describe_window,
    _fetch_status,
    _format_event,
    _format_rollup_line,
    _format_trade_line,
    _next_weekly_run,
    _send_daily_pnl_summary,
    _send_weekly_pnl_summary,
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
    line = _format_rollup_line("Hôm nay", stats, equity_usdt=Decimal("114.78"))
    assert line == "Hôm nay: -0.2200 USDT (-0.19%) | 1 lệnh, tỷ lệ thắng 0%"


def test_format_rollup_line_omits_pct_when_equity_unknown():
    stats = _WindowStats(pnl_usdt=Decimal("-0.22"), wins=0, losses=1)
    line = _format_rollup_line("Hôm nay", stats, equity_usdt=None)
    assert "%" not in line.split("|")[0]
    assert line.startswith("Hôm nay: -0.2200 USDT | 1 lệnh")


def test_format_rollup_line_zero_equity_does_not_divide_by_zero():
    stats = _WindowStats(pnl_usdt=Decimal("1"), wins=1, losses=0)
    line = _format_rollup_line("Hôm nay", stats, equity_usdt=Decimal("0"))
    assert "%" not in line.split("|")[0]


def test_format_rollup_line_omits_win_rate_when_no_trades():
    line = _format_rollup_line("Hôm nay", _WindowStats(Decimal("0"), 0, 0), equity_usdt=None)
    assert "tỷ lệ thắng" not in line
    assert line == "Hôm nay: 0.0000 USDT | 0 lệnh"


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


def test_llm_timeout_streak_is_relayed_to_telegram():
    assert AuditEventType.LLM_TIMEOUT_STREAK.value in _NOTIFY_EVENT_TYPES
    assert AuditEventType.LLM_TIMEOUT_RECOVERED.value in _NOTIFY_EVENT_TYPES


def test_format_event_llm_timeout_streak():
    trace_id = uuid.uuid4()
    event = AuditEvent(
        trace_id=trace_id,
        event_type=AuditEventType.LLM_TIMEOUT_STREAK.value,
        payload={"streak": 5, "threshold": 5, "symbol": "ETH/USDT", "reason": "llm_timeout"},
    )
    text = _format_event(event)
    assert "LLM KHÔNG PHẢN HỒI" in text
    assert "5 lần liên tiếp" in text
    assert "llm_timeout, ETH/USDT" in text
    assert "trace_id" not in text and str(trace_id) not in text


def test_format_event_llm_timeout_recovered():
    event = AuditEvent(
        trace_id=uuid.uuid4(),
        event_type=AuditEventType.LLM_TIMEOUT_RECOVERED.value,
        payload={"recovered_after": 7, "symbol": "BTC/USDT"},
    )
    text = _format_event(event)
    assert "LLM đã hoạt động lại" in text
    assert "sau 7 lần lỗi liên tiếp" in text


def test_format_event_buy_is_short_and_has_no_trace_id():
    event = AuditEvent(
        trace_id=uuid.uuid4(),
        event_type=AuditEventType.POSITION_OPENED.value,
        payload={"pair": "BTC/USDT", "entry_price": "50000.00000000", "amount": "0.01000000"},
    )
    assert _format_event(event) == (
        "🟢 MUA BTC/USDT\nGiá: 50000\nSố lượng: 0.01 (≈500.00 USDT)"
    )


def test_format_event_buy_from_reconciliation_omits_missing_fields():
    event = AuditEvent(
        trace_id=uuid.uuid4(),
        event_type=AuditEventType.POSITION_OPENED.value,
        payload={"pair": "ETH/USDT", "source": "reconciliation"},
    )
    assert _format_event(event) == "🟢 MUA ETH/USDT"


def test_format_event_sell_with_profit():
    event = AuditEvent(
        trace_id=uuid.uuid4(),
        event_type=AuditEventType.POSITION_CLOSED.value,
        payload={
            "pair": "BTC/USDT",
            "pnl_usdt": "1.5",
            "exit_reason": "trailing_stop_loss",
            "r_multiple": "0.75",
            "fees_usdt": "0.12",
        },
    )
    assert _format_event(event) == (
        "✅ BÁN BTC/USDT\nLãi: +1.50 USDT (+0.75R)\nLý do: Cắt lỗ đuổi\nPhí: 0.12 USDT"
    )


def test_format_event_sell_with_loss_and_unknown_exit_reason():
    event = AuditEvent(
        trace_id=uuid.uuid4(),
        event_type=AuditEventType.POSITION_CLOSED.value,
        payload={"pair": "SOL/USDT", "pnl_usdt": "-0.22", "exit_reason": "custom_tag"},
    )
    assert _format_event(event) == "🔴 BÁN SOL/USDT\nLỗ: -0.22 USDT\nLý do: custom_tag"


def test_next_weekly_run_advances_to_next_matching_weekday():
    # Wednesday 2026-08-12 -> next Monday (weekday 0) 08:00 UTC.
    now = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    next_run = _next_weekly_run(now, weekday=0, hour=8)
    assert next_run == datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)


def test_next_weekly_run_same_day_before_hour_runs_today():
    # Monday 2026-08-17, 06:00 UTC, target Monday 08:00 -> still today.
    now = datetime(2026, 8, 17, 6, 0, tzinfo=timezone.utc)
    next_run = _next_weekly_run(now, weekday=0, hour=8)
    assert next_run == datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)


def test_next_weekly_run_same_day_after_hour_rolls_to_next_week():
    # Monday 2026-08-17, 09:00 UTC, target Monday 08:00 already passed
    # today -> next Monday, not today.
    now = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    next_run = _next_weekly_run(now, weekday=0, hour=8)
    assert next_run == datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)


def test_describe_window_spells_out_wins_losses_and_pct_of_equity():
    stats = _WindowStats(pnl_usdt=Decimal("-0.22"), wins=0, losses=1)
    text = _describe_window(stats, equity_usdt=Decimal("114.78"))
    assert text == (
        "-0.2200 USDT từ 1 lệnh đã đóng (0 thắng, 1 thua, tỷ lệ thắng 0%)"
        " — tương đương -0.19% tổng tài sản hiện tại"
    )


def test_describe_window_no_equity():
    stats = _WindowStats(pnl_usdt=Decimal("3.0"), wins=1, losses=0)
    text = _describe_window(stats, equity_usdt=None)
    assert text == "3.0000 USDT từ 1 lệnh đã đóng (1 thắng, 0 thua, tỷ lệ thắng 100%)"


def test_describe_window_empty_window():
    text = _describe_window(_WindowStats(Decimal("0"), 0, 0), equity_usdt=Decimal("100"))
    assert text == "0.0000 USDT (không có lệnh nào đóng trong giai đoạn này)"


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


class _FakeEmail:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []

    async def send_email(self, subject: str, text_body: str, html_body: str | None = None) -> bool:
        self.sent.append((subject, text_body, html_body))
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
    assert "Hôm nay: -0.2200 USDT" in text
    assert "SOL/USDT: 100" in text
    assert "ETH/USDT" not in text.split("7 ngày qua")[0]  # not in the today section
    assert "7 ngày qua: 2.7800 USDT" in text  # -0.22 + 3.0
    assert "Từ khi chạy thật (" in text
    assert "3.7800 USDT" in text  # -0.22 + 3.0 + 1.0, XRP's 999 excluded
    assert "999" not in text
    assert "Tài sản: 114.78 USDT | Vị thế đang mở: 2" in text


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
    assert "Hôm nay: 0.0000 USDT | 0 lệnh" in text
    assert "Tài sản:" not in text


async def test_weekly_summary_rolls_up_this_week_prev_week_and_since_live_separately(
    db_session_factory, monkeypatch
):
    live_start = NOW - timedelta(days=60)
    async with db_session_factory() as session:
        # This week (trailing 7d): one loss.
        await _make_closed_position(
            session,
            symbol="SOL/USDT",
            pnl_usdt=Decimal("-0.22"),
            pnl_pct=Decimal("-0.0099"),
            closed_at=NOW - timedelta(days=2),
        )
        # Previous week (7-14d ago), still since live: one win.
        await _make_closed_position(
            session,
            symbol="ETH/USDT",
            pnl_usdt=Decimal("3.0"),
            pnl_pct=Decimal("0.01"),
            closed_at=NOW - timedelta(days=10),
        )
        # Since live but outside both weekly windows: one win.
        await _make_closed_position(
            session,
            symbol="BTC/USDT",
            pnl_usdt=Decimal("1.0"),
            pnl_pct=Decimal("0.005"),
            closed_at=NOW - timedelta(days=40),
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

    email = _FakeEmail()
    settings = NotifierSettings(live_trading_started_at=live_start.isoformat())
    monkeypatch.setattr(
        "notifier.app.main._fetch_status",
        lambda _settings: _fake_status(),
    )

    await _send_weekly_pnl_summary(db_session_factory, email, settings, NOW)

    assert len(email.sent) == 1
    subject, text, html = email.sent[0]
    assert "TradeMind - Báo cáo tuần" in subject

    # Plain-text body: verbose, self-explanatory per-window descriptions.
    assert (
        "Tuần này (7 ngày qua): -0.2200 USDT từ 1 lệnh đã đóng "
        "(0 thắng, 1 thua, tỷ lệ thắng 0%)" in text
    )
    assert "-0.19% tổng tài sản hiện tại" in text
    assert "SOL/USDT: 100" in text
    assert "ETH/USDT" not in text.split("Tuần trước")[0]  # not in the this-week section
    assert (
        "Tuần trước (7 ngày trước đó): 3.0000 USDT từ 1 lệnh đã đóng "
        "(1 thắng, 0 thua, tỷ lệ thắng 100%)" in text
    )
    assert "Từ khi bắt đầu giao dịch thật (" in text
    # -0.22 + 3.0 + 1.0 = 3.78, XRP's 999 excluded (pre-live).
    assert "3.7800 USDT từ 3 lệnh đã đóng (2 thắng, 1 thua, tỷ lệ thắng 67%)" in text
    assert "999" not in text
    assert "Tổng tài sản hiện tại: 114.78 USDT" in text
    assert "Vị thế đang mở: 2" in text

    # HTML alternative: same underlying numbers, rendered as stat cards.
    assert html is not None
    assert "SOL/USDT" in html
    assert "2 thắng / 1 thua" in html  # since-live tile: 2 wins, 1 loss
    assert "114.78" in html
    assert "999" not in html


async def test_weekly_summary_still_sends_when_status_unavailable(db_session_factory, monkeypatch):
    email = _FakeEmail()
    settings = NotifierSettings(live_trading_started_at=(NOW - timedelta(days=7)).isoformat())
    monkeypatch.setattr(
        "notifier.app.main._fetch_status", lambda _settings: _none_status()
    )

    await _send_weekly_pnl_summary(db_session_factory, email, settings, NOW)

    assert len(email.sent) == 1
    _subject, text, html = email.sent[0]
    assert "Tuần này (7 ngày qua): 0.0000 USDT (không có lệnh nào đóng trong giai đoạn này)" in text
    assert "Tổng tài sản hiện tại:" not in text
    assert html is not None
    assert "Tình trạng tài khoản" not in html


async def _none_status():
    return None


async def test_fetch_status_returns_none_on_http_error():
    settings = NotifierSettings(admin_api_url="http://127.0.0.1:1", admin_api_key="x")
    assert await _fetch_status(settings) is None
