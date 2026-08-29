"""Positive-expectancy plan M3 — Performance Engine math.

Pure-function tests (no Postgres): the ORM loader is covered by
`services/admin_api/tests/test_performance.py`.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from common.performance import (
    SCORE_BUCKETS,
    ClosedTradeMetrics,
    compute_avg_drawdown_pct,
    compute_avg_loss_r,
    compute_avg_win_r,
    compute_expectancy_r,
    compute_max_drawdown_pct,
    compute_profit_factor,
    compute_total_r,
    compute_win_rate,
    score_bucket_label,
    summarize,
    summarize_breakdowns,
)

_T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _trade(
    pnl: str,
    *,
    r: str | None = None,
    fees: str | None = "0.10",
    minutes: int = 0,
    regime: str | None = None,
    volatility: str | None = None,
    score: int | None = None,
) -> ClosedTradeMetrics:
    return ClosedTradeMetrics(
        pnl_usdt=Decimal(pnl),
        r_multiple=None if r is None else Decimal(r),
        fees_usdt=None if fees is None else Decimal(fees),
        closed_at=_T0 + timedelta(minutes=minutes),
        market_regime=regime,
        volatility_regime=volatility,
        trade_score=score,
    )


def test_expectancy_matches_the_plan_worked_example() -> None:
    """Vision doc / issue #4: 0.45 * 2.1 + 0.55 * -1.0 = +0.395R.

    20 trades, 9 winners at +2.1R, 11 losers at -1.0R.
    """
    trades = [_trade("10", r="2.1", minutes=i) for i in range(9)] + [
        _trade("-5", r="-1.0", minutes=9 + i) for i in range(11)
    ]

    assert compute_win_rate(trades) == Decimal("0.45")
    assert compute_avg_win_r(trades) == Decimal("2.1")
    assert compute_avg_loss_r(trades) == Decimal("-1.0")
    assert compute_expectancy_r(trades) == Decimal("0.395")
    # win_rate * avg_win_r + loss_rate * avg_loss_r, the vision doc's own
    # decomposition, lands on the same number.
    decomposed = Decimal("0.45") * Decimal("2.1") + Decimal("0.55") * Decimal("-1.0")
    assert decomposed == Decimal("0.395")


def test_empty_history_is_all_none_not_zero() -> None:
    assert compute_win_rate([]) is None
    assert compute_expectancy_r([]) is None
    assert compute_profit_factor([]) is None
    report = summarize([], starting_equity_usdt=Decimal("1000"))
    assert report.trades == 0
    assert report.expectancy_r is None
    assert report.total_pnl_usdt == Decimal("0")


def test_breakeven_trade_counts_in_denominator_only() -> None:
    trades = [_trade("5", r="1.0"), _trade("0", r="0"), _trade("-5", r="-1.0")]
    assert compute_win_rate(trades) == Decimal("1") / Decimal("3")
    report = summarize(trades, starting_equity_usdt=Decimal("1000"))
    assert (report.wins, report.losses, report.breakeven) == (1, 1, 1)


def test_r_metrics_exclude_trades_without_an_r_multiple() -> None:
    """D5: a legacy trade with no `r_multiple` is dropped from every R
    metric rather than counted as 0R."""
    trades = [_trade("10", r="2.0"), _trade("-5", r=None), _trade("-5", r="-1.0")]

    assert compute_total_r(trades) == Decimal("1.0")
    assert compute_expectancy_r(trades) == Decimal("0.5")  # (2.0 - 1.0) / 2
    report = summarize(trades, starting_equity_usdt=Decimal("1000"))
    assert report.trades == 3
    assert report.trades_with_r == 2


def test_profit_factor_is_none_without_a_losing_trade() -> None:
    assert compute_profit_factor([_trade("10", r="2.0"), _trade("5", r="1.0")]) is None


def test_profit_factor_ratio() -> None:
    trades = [_trade("30"), _trade("-10"), _trade("-5")]
    assert compute_profit_factor(trades) == Decimal("2")


def test_drawdown_needs_an_equity_anchor() -> None:
    trades = [_trade("10", r="1.0"), _trade("-30", r="-3.0"), _trade("5", r="0.5")]
    assert compute_max_drawdown_pct(trades, starting_equity_usdt=Decimal("0")) is None

    report_no_anchor = summarize(trades, starting_equity_usdt=None)
    assert report_no_anchor.max_drawdown_pct is None
    assert report_no_anchor.avg_drawdown_pct is None
    # every non-drawdown metric is still populated
    assert report_no_anchor.expectancy_r == Decimal("-0.5")

    report = summarize(trades, starting_equity_usdt=Decimal("100"))
    # curve: 110, 80, 85 against a peak of 110 -> worst (110-80)/110
    assert report.max_drawdown_pct == Decimal("30") / Decimal("110")


def test_drawdown_is_zero_when_curve_never_dips() -> None:
    trades = [_trade("10", r="1.0"), _trade("5", r="0.5")]
    report = summarize(trades, starting_equity_usdt=Decimal("100"))
    assert report.max_drawdown_pct == Decimal("0")
    assert report.avg_drawdown_pct == Decimal("0")


def test_drawdown_orders_by_close_time_not_list_order() -> None:
    later_big_loss = _trade("-40", r="-4.0", minutes=100)
    earlier_win = _trade("20", r="2.0", minutes=1)
    report = summarize([later_big_loss, earlier_win], starting_equity_usdt=Decimal("100"))
    # chronological curve: 120 then 80 -> drawdown (120-80)/120
    assert report.max_drawdown_pct == Decimal("40") / Decimal("120")


def test_total_slippage_is_none_never_zero() -> None:
    report = summarize([_trade("10", r="1.0")], starting_equity_usdt=Decimal("100"))
    assert report.total_slippage_usdt is None


def test_fees_sum_ignores_missing_values() -> None:
    trades = [_trade("10", r="1.0", fees="0.25"), _trade("-5", r="-1.0", fees=None)]
    assert summarize(trades, starting_equity_usdt=Decimal("100")).total_fees_usdt == Decimal("0.25")


# --- M4: score buckets + summarize_breakdowns --------------------------


def test_score_bucket_label_edges() -> None:
    assert score_bucket_label(0) == "0–39"
    assert score_bucket_label(39) == "0–39"
    assert score_bucket_label(40) == "40–69"
    assert score_bucket_label(69) == "40–69"
    assert score_bucket_label(70) == "70–100"
    assert score_bucket_label(100) == "70–100"
    assert score_bucket_label(None) == "(unscored)"
    assert score_bucket_label(101) == "(unscored)"  # out of range, defensive
    # the three bands tile 0..100 with no gap or overlap
    assert [low for _, low, _ in SCORE_BUCKETS] == [0, 40, 70]
    assert [high for _, _, high in SCORE_BUCKETS] == [39, 69, 100]


def test_summarize_breakdowns_groups_each_dimension() -> None:
    trades = [
        _trade("10", r="2.0", regime="trend_pullback", volatility="NORMAL", score=80),
        _trade(
            "-5", r="-1.0", regime="trend_pullback",
            volatility="HIGH_VOLATILITY", score=45, minutes=1,
        ),
        _trade("4", r="0.5", regime="mean_reversion", volatility="NORMAL", score=30, minutes=2),
        _trade("6", r="0.7", regime=None, volatility=None, score=None, minutes=3),
    ]
    b = summarize_breakdowns(trades, starting_equity_usdt=Decimal("1000"))

    regimes = {c.key: c.report.trades for c in b.by_regime}
    assert regimes == {"trend_pullback": 2, "mean_reversion": 1, "(unclassified)": 1}
    # per-cohort expectancy is just summarize() over that cohort
    tp = next(c for c in b.by_regime if c.key == "trend_pullback")
    assert tp.report.expectancy_r == Decimal("0.5")  # (2.0 + -1.0) / 2

    vol = {c.key: c.report.trades for c in b.by_volatility}
    assert vol == {"NORMAL": 2, "HIGH_VOLATILITY": 1, "(unclassified)": 1}

    buckets = {c.key: c.report.trades for c in b.by_score_bucket}
    assert buckets == {"0–39": 1, "40–69": 1, "70–100": 1, "(unscored)": 1}


def test_summarize_breakdowns_orders_by_count_catch_all_last() -> None:
    trades = (
        [_trade("1", r="0.1", regime="mean_reversion", minutes=i) for i in range(3)]
        + [_trade("1", r="0.1", regime="trend_pullback", minutes=10 + i) for i in range(1)]
        + [_trade("1", r="0.1", regime=None, minutes=20 + i) for i in range(5)]
    )
    keys = [c.key for c in summarize_breakdowns(trades, starting_equity_usdt=None).by_regime]
    # biggest known cohort first, then the smaller one, "(unclassified)" always last
    assert keys == ["mean_reversion", "trend_pullback", "(unclassified)"]


def test_summarize_breakdowns_cohort_counts_reconcile_with_parent() -> None:
    trades = [
        _trade("5", r="1.0", regime="trend_pullback", volatility="NORMAL", score=72, minutes=0),
        _trade("-5", r="-1.0", regime="mean_reversion", score=10, minutes=1),
        _trade("2", r=None, minutes=2),
    ]
    parent = summarize(trades, starting_equity_usdt=Decimal("500"))
    b = summarize_breakdowns(trades, starting_equity_usdt=Decimal("500"))
    for cohorts in (b.by_regime, b.by_volatility, b.by_score_bucket):
        assert sum(c.report.trades for c in cohorts) == parent.trades


@given(
    st.lists(
        st.tuples(
            st.decimals(min_value="-500", max_value="500", places=2, allow_nan=False),
            st.decimals(min_value="-10", max_value="10", places=4, allow_nan=False),
        ),
        min_size=1,
        max_size=60,
    )
)
def test_expectancy_r_sits_between_worst_and_best_r(rows: list[tuple[Decimal, Decimal]]) -> None:
    trades = [
        _trade(str(pnl), r=str(r), minutes=i) for i, (pnl, r) in enumerate(rows)
    ]
    expectancy = compute_expectancy_r(trades)
    r_values = [Decimal(str(r)) for _, r in rows]
    assert expectancy is not None
    assert min(r_values) <= expectancy <= max(r_values)


@given(
    st.lists(
        st.decimals(min_value="-500", max_value="500", places=2, allow_nan=False),
        min_size=1,
        max_size=60,
    ),
    st.decimals(min_value="1", max_value="100000", places=2, allow_nan=False),
)
def test_max_drawdown_is_non_negative_and_bounds_avg_drawdown(
    pnls: list[Decimal], equity: Decimal
) -> None:
    # A drawdown fraction can exceed 1 only if the equity curve goes
    # negative (cumulative loss > the anchor) — impossible for a spot
    # long-only account in practice, so the only hard invariants are
    # non-negativity and that the mean underwater depth never exceeds the
    # single worst point.
    trades = [_trade(str(pnl), r="1.0", minutes=i) for i, pnl in enumerate(pnls)]
    drawdown = compute_max_drawdown_pct(trades, starting_equity_usdt=Decimal(str(equity)))
    assert drawdown is not None
    assert drawdown >= Decimal("0")
    avg = compute_avg_drawdown_pct(trades, starting_equity_usdt=Decimal(str(equity)))
    assert avg is not None
    # `2*d/2 != d` at 28-digit Decimal precision, so compare with a wide
    # tolerance — the invariant under test is "mean depth doesn't exceed the
    # worst point", not bit-exact division.
    epsilon = Decimal("1e-12")
    assert avg >= Decimal("0")
    assert avg <= drawdown + epsilon
