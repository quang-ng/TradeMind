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

from collections.abc import Sequence
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
