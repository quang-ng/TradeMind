"""Backtest / live parity (positive-expectancy plan M4, Section 7):
`report.metrics_from_closed_trades` + `common.performance` must produce the
exact numbers you get from `common.performance` directly — the property the
whole "no strategy change deployed straight from backtest results" stance
depends on.
"""

import _bootstrap  # noqa: F401,I001 -- must patch sys.path before the imports below
from datetime import datetime, timezone
from decimal import Decimal

import report as perf_report  # noqa: E402
from common.performance import (  # noqa: E402
    ClosedTradeMetrics,
    summarize,
    summarize_breakdowns,
)
from ledger import ClosedTrade  # noqa: E402

_ENTRY_A = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
_ENTRY_B = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)
_ENTRY_C = datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc)
_EXIT_A = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
_EXIT_B = datetime(2026, 8, 1, 5, 0, tzinfo=timezone.utc)
_EXIT_C = datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc)


def _closed(symbol: str, pnl: str, r: str | None, entry: datetime, exit_: datetime) -> ClosedTrade:
    return ClosedTrade(
        symbol=symbol,
        entry_time=entry,
        exit_time=exit_,
        entry_price=Decimal("100"),
        exit_price=Decimal("101"),
        size_usdt=Decimal("100"),
        pnl_usdt=Decimal(pnl),
        pnl_pct=Decimal(pnl) / Decimal("100"),
        exit_reason="llm_sell_signal",
        r_multiple=None if r is None else Decimal(r),
    )


def _fixture():
    trades = [
        _closed("BTC/USDT", "12.00", "1.5", _ENTRY_A, _EXIT_A),
        _closed("ETH/USDT", "-4.00", "-1.0", _ENTRY_B, _EXIT_B),
        _closed("SOL/USDT", "3.00", None, _ENTRY_C, _EXIT_C),  # legacy: no R
    ]
    regimes = {
        ("BTC/USDT", _ENTRY_A.isoformat()): "trend_pullback",
        ("ETH/USDT", _ENTRY_B.isoformat()): "mean_reversion",
        ("SOL/USDT", _ENTRY_C.isoformat()): "unknown",  # -> "(unclassified)"
    }
    scores = {
        ("BTC/USDT", _ENTRY_A.isoformat()): 80,
        ("ETH/USDT", _ENTRY_B.isoformat()): 45,
    }
    volatilities = {
        ("BTC/USDT", _ENTRY_A.isoformat()): "NORMAL",
        ("ETH/USDT", _ENTRY_B.isoformat()): "HIGH_VOLATILITY",
    }
    expected = [
        ClosedTradeMetrics(
            pnl_usdt=Decimal("12.00"), r_multiple=Decimal("1.5"), fees_usdt=None,
            closed_at=_EXIT_A, market_regime="trend_pullback",
            volatility_regime="NORMAL", trade_score=80,
        ),
        ClosedTradeMetrics(
            pnl_usdt=Decimal("-4.00"), r_multiple=Decimal("-1.0"), fees_usdt=None,
            closed_at=_EXIT_B, market_regime="mean_reversion",
            volatility_regime="HIGH_VOLATILITY", trade_score=45,
        ),
        ClosedTradeMetrics(
            pnl_usdt=Decimal("3.00"), r_multiple=None, fees_usdt=None,
            closed_at=_EXIT_C, market_regime=None,
            volatility_regime=None, trade_score=None,
        ),
    ]
    return trades, regimes, scores, volatilities, expected


def test_metrics_from_closed_trades_matches_hand_built_rows():
    trades, regimes, scores, volatilities, expected = _fixture()
    assert (
        perf_report.metrics_from_closed_trades(
            trades, regimes=regimes, scores=scores, volatilities=volatilities
        )
        == expected
    )


def test_summary_and_breakdowns_are_identical_via_script_and_directly():
    trades, regimes, scores, volatilities, expected = _fixture()
    via_script = perf_report.metrics_from_closed_trades(
        trades, regimes=regimes, scores=scores, volatilities=volatilities
    )
    anchor = Decimal("1000")

    assert summarize(via_script, starting_equity_usdt=anchor) == summarize(
        expected, starting_equity_usdt=anchor
    )
    assert summarize_breakdowns(via_script, starting_equity_usdt=anchor) == summarize_breakdowns(
        expected, starting_equity_usdt=anchor
    )


def test_render_helpers_emit_the_expected_blocks():
    trades, regimes, scores, volatilities, _ = _fixture()
    metrics = perf_report.metrics_from_closed_trades(
        trades, regimes=regimes, scores=scores, volatilities=volatilities
    )
    report = summarize(metrics, starting_equity_usdt=Decimal("1000"))
    summary_text = perf_report.render_summary(report)
    assert "ALL" in summary_text
    assert "expR=" in summary_text
    assert "without an R-multiple" in summary_text  # the SOL row

    breakdown_text = perf_report.render_breakdowns(
        summarize_breakdowns(metrics, starting_equity_usdt=Decimal("1000"))
    )
    for heading in ("By setup regime", "By volatility regime", "By trade-score bucket"):
        assert heading in breakdown_text
    assert "(unclassified)" in breakdown_text
