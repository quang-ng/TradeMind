"""Positive-expectancy plan M5 — the ORM-aware half of the Historical
Expectancy Filter, mirroring `account_state.py`.

`load_expectancy_state` does the one Postgres read and the
`common.performance` aggregation the filter needs, producing a frozen
`ExpectancyView` *before* the pure `evaluate()` runs — the same
pre-fetch-then-pass-in pattern already used for `AccountState`, which keeps
`risk_engine/app/rules/*` a pure, deterministic function of its inputs
(PROJECT.md Section 9).

The cohort ("setup") is keyed by the signal's market regime
(`Signal.setup_regime`, denormalized onto `Position.market_regime` at open)
and its trade-score bucket (`common.performance.SCORE_BUCKETS`, the same
edges `GET /performance`'s breakdown and the frontend score filter use). If
the deferred `setup_expectancy_stats` materialized table is ever built
(implementation plan Section 3), only `load_expectancy_state`'s query body
changes — nothing downstream.
"""

from collections.abc import Sequence

from common.performance import (
    SCORE_BUCKETS,
    ClosedTradeMetrics,
    compute_expectancy_r,
    score_bucket_label,
)
from common.performance_query import load_closed_trade_metrics
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import ExpectancyView


def setup_key(*, market_regime: str | None, trade_score: int | None) -> str:
    """Stable, human-readable label for the (regime, score-bucket) cohort,
    reused verbatim in the `expectancy_check` audit payload so a shadow-mode
    decision can be traced back to exactly which historical slice was
    consulted."""
    regime = market_regime or "(unclassified)"
    return f"{regime} | {score_bucket_label(trade_score)}"


def _score_bucket_bounds(trade_score: int | None) -> tuple[int, int] | None:
    """The inclusive `[low, high]` score range `trade_score` falls in, or
    `None` when it has no score or sits outside every defined bucket — in
    which case there is no queryable cohort and the filter abstains."""
    if trade_score is None:
        return None
    for _label, low, high in SCORE_BUCKETS:
        if low <= trade_score <= high:
            return low, high
    return None


def build_expectancy_view(
    trades: Sequence[ClosedTradeMetrics], *, key: str
) -> ExpectancyView:
    """Pure: collapse a cohort's closed trades into the `ExpectancyView` the
    filter reads. `sample_size` counts only R-tracked trades — the same set
    `compute_expectancy_r` averages over — so the minimum-sample gate and
    the expectancy figure never disagree about what the sample is."""
    r_tracked = sum(1 for t in trades if t.r_multiple is not None)
    return ExpectancyView(
        setup_key=key,
        sample_size=r_tracked,
        expectancy_r=compute_expectancy_r(trades),
    )


async def load_expectancy_state(
    session: AsyncSession,
    *,
    market_regime: str | None,
    trade_score: int | None,
) -> ExpectancyView:
    """Historical expectancy for the current signal's setup cohort. An
    empty view (`sample_size=0`, `expectancy_r=None`) when the signal has no
    regime or no in-range score — the filter reads that as "abstain", never
    "reject" (implementation plan Section 4, M5)."""
    key = setup_key(market_regime=market_regime, trade_score=trade_score)
    bounds = _score_bucket_bounds(trade_score)
    if market_regime is None or bounds is None:
        return ExpectancyView(setup_key=key, sample_size=0, expectancy_r=None)

    score_min, score_max = bounds
    trades = await load_closed_trade_metrics(
        session,
        regime=market_regime,
        score_min=score_min,
        score_max=score_max,
    )
    return build_expectancy_view(trades, key=key)
