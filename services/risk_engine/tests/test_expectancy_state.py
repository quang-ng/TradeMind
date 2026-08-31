"""Unit tests for the pure half of `expectancy_state.py` — the setup-key
labelling, score-bucket bounds, and cohort aggregation. `load_expectancy_state`
itself (the Postgres read) is exercised end-to-end in `test_main_integration.py`.
"""

from datetime import datetime, timezone
from decimal import Decimal

from common.performance import ClosedTradeMetrics

from risk_engine.app.expectancy_state import (
    _score_bucket_bounds,
    build_expectancy_view,
    setup_key,
)

CLOSED_AT = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _trade(*, pnl: str, r: str | None) -> ClosedTradeMetrics:
    return ClosedTradeMetrics(
        pnl_usdt=Decimal(pnl),
        r_multiple=None if r is None else Decimal(r),
        fees_usdt=None,
        closed_at=CLOSED_AT,
    )


# --- setup_key -----------------------------------------------------------


def test_setup_key_combines_regime_and_score_bucket():
    assert setup_key(market_regime="trend_pullback", trade_score=84) == (
        "trend_pullback | 70–100"
    )


def test_setup_key_labels_missing_regime_and_score():
    assert setup_key(market_regime=None, trade_score=None) == "(unclassified) | (unscored)"


# --- _score_bucket_bounds ----------------------------------------------


def test_score_bucket_bounds_maps_score_to_inclusive_range():
    assert _score_bucket_bounds(0) == (0, 39)
    assert _score_bucket_bounds(39) == (0, 39)
    assert _score_bucket_bounds(40) == (40, 69)
    assert _score_bucket_bounds(75) == (70, 100)
    assert _score_bucket_bounds(100) == (70, 100)


def test_score_bucket_bounds_is_none_without_a_usable_score():
    assert _score_bucket_bounds(None) is None
    assert _score_bucket_bounds(-1) is None
    assert _score_bucket_bounds(101) is None


# --- build_expectancy_view -------------------------------------------


def test_build_expectancy_view_averages_only_r_tracked_trades():
    trades = [
        _trade(pnl="10", r="1.0"),
        _trade(pnl="-5", r="-0.5"),
        _trade(pnl="20", r="2.0"),
    ]
    view = build_expectancy_view(trades, key="k")
    assert view.setup_key == "k"
    assert view.sample_size == 3
    # (1.0 - 0.5 + 2.0) / 3
    assert view.expectancy_r == Decimal("2.5") / Decimal("3")


def test_build_expectancy_view_excludes_trades_without_an_r_multiple():
    trades = [
        _trade(pnl="10", r="1.0"),
        _trade(pnl="-5", r=None),  # legacy row (D5) — not counted, not 0R
        _trade(pnl="8", r="0.8"),
    ]
    view = build_expectancy_view(trades, key="k")
    assert view.sample_size == 2
    assert view.expectancy_r == Decimal("1.8") / Decimal("2")


def test_build_expectancy_view_empty_cohort_has_no_expectancy():
    view = build_expectancy_view([], key="k")
    assert view.sample_size == 0
    assert view.expectancy_r is None


def test_build_expectancy_view_all_legacy_rows_has_no_expectancy():
    view = build_expectancy_view([_trade(pnl="10", r=None)], key="k")
    assert view.sample_size == 0
    assert view.expectancy_r is None
