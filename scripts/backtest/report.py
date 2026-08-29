"""Shared closed-trade -> Performance Engine reporting for the offline
backtest tools (positive-expectancy plan M4).

`mechanical_replay.py`'s run summary and the standalone
`expectancy_report.py` both turn a list of closed trades into the same
expectancy tables. Routing both through this one module — which delegates
every number to `services/common/performance.py` — is what makes "backtest
and live are provably the same math" (implementation plan Section 7) a
structural property rather than a promise.

Depends only on `common.performance`; `import _bootstrap` must have run
first (as every `scripts/backtest` entrypoint already does).
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Protocol

from common.performance import (
    BreakdownCohort,
    ClosedTradeMetrics,
    PerformanceBreakdowns,
    PerformanceReport,
)

# (symbol, entry_time.isoformat()) — the join key mechanical_replay already
# builds its `trade_regimes` dict on.
TradeKey = tuple[str, str]

# Sentinel `mechanical_replay` uses for "no regime recorded for this trade";
# normalized to None here so the trade lands in the "(unclassified)" cohort
# rather than a literal "unknown" one.
_UNKNOWN_REGIME = "unknown"


class ClosedTradeLike(Protocol):
    """The subset of `ledger.ClosedTrade` this module reads. Duck-typed so
    `report.py` stays free of the `risk_engine` import chain `ledger` pulls
    in."""

    symbol: str

    @property
    def entry_time(self): ...

    @property
    def exit_time(self): ...

    pnl_usdt: Decimal
    r_multiple: Decimal | None


def metrics_from_closed_trades(
    trades: Sequence[ClosedTradeLike],
    *,
    regimes: Mapping[TradeKey, str] | None = None,
    scores: Mapping[TradeKey, int] | None = None,
    volatilities: Mapping[TradeKey, str] | None = None,
) -> list[ClosedTradeMetrics]:
    """Map ledger closed trades + the per-trade journal dimensions
    `mechanical_replay` collects into `common.performance` rows. Fees are
    already netted into `ClosedTrade.pnl_usdt` by the ledger, so
    `fees_usdt` is left `None` (not double-counted)."""
    regimes = regimes or {}
    scores = scores or {}
    volatilities = volatilities or {}
    rows: list[ClosedTradeMetrics] = []
    for trade in trades:
        key = (trade.symbol, trade.entry_time.isoformat())
        regime = regimes.get(key)
        rows.append(
            ClosedTradeMetrics(
                pnl_usdt=trade.pnl_usdt,
                r_multiple=trade.r_multiple,
                fees_usdt=None,
                closed_at=trade.exit_time,
                market_regime=None if regime in (None, _UNKNOWN_REGIME) else regime,
                volatility_regime=volatilities.get(key),
                trade_score=scores.get(key),
            )
        )
    return rows


def _pct(value: Decimal | None) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def _r(value: Decimal | None) -> str:
    return "—" if value is None else f"{float(value):+.2f}"


def _pf(value: Decimal | None) -> str:
    return "—" if value is None else f"{float(value):.2f}"


def _row(label: str, report: PerformanceReport) -> str:
    return (
        f"  {label:<22} n={report.trades:<4} win={_pct(report.win_rate):>7}  "
        f"expR={_r(report.expectancy_r):>7}  avgW={_r(report.avg_win_r):>7}  "
        f"avgL={_r(report.avg_loss_r):>7}  totR={_r(report.total_r):>8}  "
        f"PF={_pf(report.profit_factor):>6}  pnl={float(report.total_pnl_usdt):>9.2f}"
    )


def render_summary(report: PerformanceReport) -> str:
    lines = ["=== Expectancy (R-normalized, via common/performance.py) ===", _row("ALL", report)]
    untracked = report.trades - report.trades_with_r
    if untracked:
        lines.append(
            f"  ({untracked} trade(s) without an R-multiple — excluded from every R metric)"
        )
    return "\n".join(lines)


def _block(title: str, cohorts: Sequence[BreakdownCohort]) -> str:
    lines = [f"\n=== {title} ==="]
    if not cohorts:
        lines.append("  (no closed trades)")
    lines.extend(_row(cohort.key, cohort.report) for cohort in cohorts)
    return "\n".join(lines)


def render_breakdowns(breakdowns: PerformanceBreakdowns) -> str:
    return "\n".join(
        [
            _block("By setup regime", breakdowns.by_regime),
            _block("By volatility regime", breakdowns.by_volatility),
            _block("By trade-score bucket", breakdowns.by_score_bucket),
        ]
    )
