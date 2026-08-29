"""Positive-expectancy plan M3 — Performance Engine.

Pure functions computing the vision doc's Phase 3 metrics (Win Rate,
Average Win/Loss R, Expectancy(R), Total R, Total PnL, Profit Factor,
Maximum/Average Drawdown, Fees) over closed-position-shaped rows.

Lives in `common`, not `admin_api`, because `scripts/backtest` needs the
exact same math (M4's "backtest and live must be provably the same math"
property this whole plan depends on — Section 7) — `common` is already "the
only code allowed to define domain models... so no service can drift from
the shared contract" (PROJECT.md Section 6); the same reasoning applies to
shared performance math.

Every function takes a `Sequence[ClosedTradeMetrics]` — not
`common.db.models.Position` directly — so this module has zero ORM/
SQLAlchemy dependency and the exact same rows can be built from a live
`Position` query (`admin_api/routers/performance.py`) or a
`scripts/backtest` CSV row (M4) without either importing the other's
machinery.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class ClosedTradeMetrics:
    """The minimal shape every `compute_*` function below needs from one
    closed trade."""

    pnl_usdt: Decimal
    # None for legacy trades that predate M1 (D5: no retroactive backfill)
    # or whose linked RiskDecision has no `actual_risk_usdt`. Every
    # R-based metric below excludes these rather than treating a missing
    # value as zero.
    r_multiple: Decimal | None
    fees_usdt: Decimal | None
    closed_at: datetime
    # Positive-expectancy plan M4 — the three journal dimensions the
    # `summarize_breakdowns` cohorts group on. Optional so every existing
    # `ClosedTradeMetrics(...)` call site and the headline `summarize()`
    # path are untouched; `None` on any dimension lands the trade in that
    # breakdown's explicit "(unclassified)"/"(unscored)" cohort rather than
    # being dropped. `market_regime` is `StrategySelector`'s label
    # (`Position.market_regime`), `volatility_regime` the M4-denormalized
    # `Position.volatility_regime`, `trade_score` the 0-100 rubric total.
    market_regime: str | None = None
    volatility_regime: str | None = None
    trade_score: int | None = None


def compute_win_rate(trades: Sequence[ClosedTradeMetrics]) -> Decimal | None:
    """Wins / total trades. `pnl_usdt == 0` (breakeven) counts toward the
    denominator but is neither a win nor a loss."""
    if not trades:
        return None
    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    return Decimal(wins) / Decimal(len(trades))


def compute_avg_win_r(trades: Sequence[ClosedTradeMetrics]) -> Decimal | None:
    wins_r = [t.r_multiple for t in trades if t.pnl_usdt > 0 and t.r_multiple is not None]
    return sum(wins_r, Decimal("0")) / Decimal(len(wins_r)) if wins_r else None


def compute_avg_loss_r(trades: Sequence[ClosedTradeMetrics]) -> Decimal | None:
    """Negative by convention (vision doc Phase 3: "Average Loss is
    negative")."""
    losses_r = [t.r_multiple for t in trades if t.pnl_usdt < 0 and t.r_multiple is not None]
    return sum(losses_r, Decimal("0")) / Decimal(len(losses_r)) if losses_r else None


def compute_total_r(trades: Sequence[ClosedTradeMetrics]) -> Decimal | None:
    values = [t.r_multiple for t in trades if t.r_multiple is not None]
    return sum(values, Decimal("0")) if values else None


def compute_expectancy_r(trades: Sequence[ClosedTradeMetrics]) -> Decimal | None:
    """`total_r / count(trades with a known R)` — mathematically the same
    number the vision doc's `win_rate * avg_win_r + loss_rate * avg_loss_r`
    decomposition produces when every trade has an R, but exact (not an
    approximation) when some trades don't (D5: legacy rows), since those
    are excluded from both the numerator and denominator consistently
    rather than silently pulled toward zero."""
    values = [t.r_multiple for t in trades if t.r_multiple is not None]
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def compute_total_pnl_usdt(trades: Sequence[ClosedTradeMetrics]) -> Decimal:
    return sum((t.pnl_usdt for t in trades), Decimal("0"))


def compute_profit_factor(trades: Sequence[ClosedTradeMetrics]) -> Decimal | None:
    """Gross profit / gross loss, in USDT. `None` (undefined) when there is
    no losing trade to divide by, rather than fabricating an infinite
    sentinel."""
    gross_profit = sum((t.pnl_usdt for t in trades if t.pnl_usdt > 0), Decimal("0"))
    gross_loss = sum((-t.pnl_usdt for t in trades if t.pnl_usdt < 0), Decimal("0"))
    return gross_profit / gross_loss if gross_loss > 0 else None


def compute_total_fees_usdt(trades: Sequence[ClosedTradeMetrics]) -> Decimal:
    return sum((t.fees_usdt for t in trades if t.fees_usdt is not None), Decimal("0"))


def _equity_curve(
    trades: Sequence[ClosedTradeMetrics], *, starting_equity_usdt: Decimal
) -> list[Decimal]:
    equity = starting_equity_usdt
    curve = []
    for trade in sorted(trades, key=lambda t: t.closed_at):
        equity += trade.pnl_usdt
        curve.append(equity)
    return curve


def compute_max_drawdown_pct(
    trades: Sequence[ClosedTradeMetrics], *, starting_equity_usdt: Decimal
) -> Decimal | None:
    """Same algorithm as `scripts/backtest/replay.py::max_drawdown_pct`
    (M4 retrofits that module to call this shared implementation instead of
    its own copy) — peak-to-trough decline of the running equity curve,
    expressed as a fraction of the peak. `starting_equity_usdt` is an
    explicit parameter, not derived internally: the offline backtest has a
    real starting balance (`Ledger.starting_equity_usdt`); the live
    endpoint anchors to the current account equity (same convention
    `GET /status`'s `daily_pnl_pct` already uses), which the caller
    resolves and passes in."""
    if not trades or starting_equity_usdt <= 0:
        return None
    peak = starting_equity_usdt
    worst = Decimal("0")
    for equity in _equity_curve(trades, starting_equity_usdt=starting_equity_usdt):
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def compute_avg_drawdown_pct(
    trades: Sequence[ClosedTradeMetrics], *, starting_equity_usdt: Decimal
) -> Decimal | None:
    """Mean depth of every point on the equity curve that sits below its
    running peak (the "underwater" curve) — distinct from max drawdown's
    single worst point. `0` (not `None`) when trades exist but the curve
    never dips below its peak; `None` only when there's nothing to compute
    from at all."""
    if not trades or starting_equity_usdt <= 0:
        return None
    peak = starting_equity_usdt
    depths = []
    for equity in _equity_curve(trades, starting_equity_usdt=starting_equity_usdt):
        peak = max(peak, equity)
        if peak > 0 and equity < peak:
            depths.append((peak - equity) / peak)
    return sum(depths, Decimal("0")) / Decimal(len(depths)) if depths else Decimal("0")


@dataclass(frozen=True)
class PerformanceReport:
    """The full M3 metric set for one cohort of closed trades. Built by
    `summarize()` from the pure `compute_*` functions above so the live
    endpoint (`admin_api/routers/performance.py`), the scheduled snapshot
    job (`scheduler/app/jobs.py`) and `scripts/backtest` (M4) all report
    the exact same numbers — Section 7's "backtest and live must be
    provably the same math" property.

    `total_slippage_usdt` is `None`, not `0`: production has no per-trade
    slippage source today (implementation plan Section 1 — it exists only in
    the offline backtest `Ledger`). A real `0` would falsely claim
    slippage-free execution; `None` says "not measured".
    """

    trades: int
    wins: int
    losses: int
    breakeven: int
    trades_with_r: int
    win_rate: Decimal | None
    avg_win_r: Decimal | None
    avg_loss_r: Decimal | None
    expectancy_r: Decimal | None
    total_r: Decimal | None
    total_pnl_usdt: Decimal
    profit_factor: Decimal | None
    max_drawdown_pct: Decimal | None
    avg_drawdown_pct: Decimal | None
    total_fees_usdt: Decimal
    total_slippage_usdt: Decimal | None
    starting_equity_usdt: Decimal | None


def summarize(
    trades: Sequence[ClosedTradeMetrics], *, starting_equity_usdt: Decimal | None
) -> PerformanceReport:
    """Compose every M3 metric over one already-filtered list of closed
    trades. `starting_equity_usdt` anchors the drawdown equity curve; when
    the caller can't resolve it (e.g. the live account-balance snapshot is
    stale) pass `None` — the two drawdown figures come back `None` and every
    other metric is unaffected."""
    drawdown_anchor = (
        starting_equity_usdt
        if starting_equity_usdt is not None and starting_equity_usdt > 0
        else None
    )
    return PerformanceReport(
        trades=len(trades),
        wins=sum(1 for t in trades if t.pnl_usdt > 0),
        losses=sum(1 for t in trades if t.pnl_usdt < 0),
        breakeven=sum(1 for t in trades if t.pnl_usdt == 0),
        trades_with_r=sum(1 for t in trades if t.r_multiple is not None),
        win_rate=compute_win_rate(trades),
        avg_win_r=compute_avg_win_r(trades),
        avg_loss_r=compute_avg_loss_r(trades),
        expectancy_r=compute_expectancy_r(trades),
        total_r=compute_total_r(trades),
        total_pnl_usdt=compute_total_pnl_usdt(trades),
        profit_factor=compute_profit_factor(trades),
        max_drawdown_pct=(
            compute_max_drawdown_pct(trades, starting_equity_usdt=drawdown_anchor)
            if drawdown_anchor is not None
            else None
        ),
        avg_drawdown_pct=(
            compute_avg_drawdown_pct(trades, starting_equity_usdt=drawdown_anchor)
            if drawdown_anchor is not None
            else None
        ),
        total_fees_usdt=compute_total_fees_usdt(trades),
        total_slippage_usdt=None,
        starting_equity_usdt=drawdown_anchor,
    )


# --- M4: expectancy broken out by journal dimension --------------------
#
# The live endpoint and the offline replay both need "which regime / which
# volatility bucket / which score band actually carries the edge" — the
# same question, the same math, one implementation (implementation plan
# Section 4 M4 / Section 7). Each cohort is just `summarize()` over a
# subset of the already-filtered trade list, so nothing here re-derives a
# metric.

# Trade-score bands. Fixed edges, matching the frontend's existing
# `PERF_SCORE_BUCKETS` filter (`frontend/src/App.tsx`) so the breakdown
# table and the score filter never disagree on where a band starts.
SCORE_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("0–39", 0, 39),
    ("40–69", 40, 69),
    ("70–100", 70, 100),
)
_UNSCORED_LABEL = "(unscored)"
_UNCLASSIFIED_LABEL = "(unclassified)"


def score_bucket_label(score: int | None) -> str:
    """The `SCORE_BUCKETS` band a trade score falls in; `"(unscored)"` for
    `None` (legacy row, D5) or an out-of-range value."""
    if score is None:
        return _UNSCORED_LABEL
    for label, low, high in SCORE_BUCKETS:
        if low <= score <= high:
            return label
    return _UNSCORED_LABEL


@dataclass(frozen=True)
class BreakdownCohort:
    """One row of a breakdown: a dimension value (`key`) and the full M3
    metric set over just that value's closed trades."""

    key: str
    report: PerformanceReport


@dataclass(frozen=True)
class PerformanceBreakdowns:
    """`summarize()` re-run per distinct value of each journal dimension,
    over one already-filtered closed-trade list. Cohorts within each list
    are ordered by descending trade count (mirroring
    `scripts/backtest/mechanical_replay.py`'s existing by-regime ordering),
    with the catch-all "(unclassified)"/"(unscored)" cohort always last."""

    by_regime: list[BreakdownCohort]
    by_volatility: list[BreakdownCohort]
    by_score_bucket: list[BreakdownCohort]


def _cohorts(
    trades: Sequence[ClosedTradeMetrics],
    *,
    key_of: Callable[[ClosedTradeMetrics], str | None],
    catch_all: str,
    starting_equity_usdt: Decimal | None,
) -> list[BreakdownCohort]:
    groups: dict[str, list[ClosedTradeMetrics]] = {}
    for trade in trades:
        raw = key_of(trade)
        groups.setdefault(raw if raw is not None else catch_all, []).append(trade)
    ordered = sorted(
        groups.items(),
        key=lambda kv: (kv[0] == catch_all, -len(kv[1]), kv[0]),
    )
    return [
        BreakdownCohort(
            key=key,
            report=summarize(cohort, starting_equity_usdt=starting_equity_usdt),
        )
        for key, cohort in ordered
    ]


def summarize_breakdowns(
    trades: Sequence[ClosedTradeMetrics], *, starting_equity_usdt: Decimal | None
) -> PerformanceBreakdowns:
    """Expectancy-by-regime / -by-volatility / -by-score-bucket over one
    cohort of closed trades. `starting_equity_usdt` anchors each cohort's
    drawdown curve exactly as it does for the parent `summarize()`."""
    return PerformanceBreakdowns(
        by_regime=_cohorts(
            trades,
            key_of=lambda t: t.market_regime,
            catch_all=_UNCLASSIFIED_LABEL,
            starting_equity_usdt=starting_equity_usdt,
        ),
        by_volatility=_cohorts(
            trades,
            key_of=lambda t: t.volatility_regime,
            catch_all=_UNCLASSIFIED_LABEL,
            starting_equity_usdt=starting_equity_usdt,
        ),
        by_score_bucket=_cohorts(
            trades,
            key_of=lambda t: score_bucket_label(t.trade_score),
            catch_all=_UNSCORED_LABEL,
            starting_equity_usdt=starting_equity_usdt,
        ),
    )
