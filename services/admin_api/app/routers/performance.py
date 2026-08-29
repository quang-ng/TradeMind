import logging
from datetime import datetime, timezone

from common import redis_keys
from common.account_balance import AccountBalanceSnapshot
from common.performance import summarize, summarize_breakdowns
from common.performance_query import load_closed_trade_metrics
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..deps import get_db_session, get_redis_client
from ..schemas import (
    PerformanceBreakdowns,
    PerformanceCohort,
    PerformanceFilters,
    PerformanceSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_api_key)])


async def _resolve_equity_anchor(redis_client: Redis) -> AccountBalanceSnapshot | None:
    """Best-effort current account equity, used only to anchor the drawdown
    equity curve (same live snapshot `GET /status` reads). Unlike `/status`,
    a missing or stale balance here is not fatal — every non-drawdown metric
    is independent of it, so the endpoint returns those and reports the two
    drawdown figures as `null` rather than failing the whole request."""
    raw = await redis_client.get(redis_keys.ACCOUNT_BALANCE_SNAPSHOT_KEY)
    if raw is None:
        return None
    try:
        balance = AccountBalanceSnapshot.model_validate_json(raw)
    except ValidationError:
        logger.warning("performance_equity_anchor_unparseable")
        return None
    if not balance.is_fresh(
        now=datetime.now(timezone.utc),
        max_age_seconds=redis_keys.ACCOUNT_BALANCE_SNAPSHOT_TTL_SECONDS,
    ):
        return None
    return balance


@router.get("/performance", response_model=PerformanceSummary)
async def get_performance(
    symbol: str | None = Query(default=None),
    regime: str | None = Query(default=None),
    score_min: int | None = Query(default=None, ge=0, le=100),
    score_max: int | None = Query(default=None, ge=0, le=100),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    redis_client: Redis = Depends(get_redis_client),
) -> PerformanceSummary:
    """PROJECT.md Section 11 — R-normalized live trading performance over the
    closed-position journal, computed on demand (current trade volume makes
    on-read aggregation fine; the materialized `setup_expectancy_stats`
    table stays deferred — implementation plan Section 3).

    Read-only: no `AuditEvent` is written (implementation plan Section 5 —
    rule 7 scopes the audit trail to state-changing writes, not reads).
    """
    if score_min is not None and score_max is not None and score_min > score_max:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="score_min must be <= score_max",
        )

    trades = await load_closed_trade_metrics(
        session,
        symbol=symbol,
        regime=regime,
        score_min=score_min,
        score_max=score_max,
        since=since,
        until=until,
    )
    balance = await _resolve_equity_anchor(redis_client)
    anchor = balance.equity_usdt if balance is not None else None
    report = summarize(trades, starting_equity_usdt=anchor)
    breakdowns = summarize_breakdowns(trades, starting_equity_usdt=anchor)
    return PerformanceSummary(
        **vars(report),
        breakdowns=PerformanceBreakdowns(
            by_regime=[
                PerformanceCohort(key=c.key, **vars(c.report)) for c in breakdowns.by_regime
            ],
            by_volatility=[
                PerformanceCohort(key=c.key, **vars(c.report))
                for c in breakdowns.by_volatility
            ],
            by_score_bucket=[
                PerformanceCohort(key=c.key, **vars(c.report))
                for c in breakdowns.by_score_bucket
            ],
        ),
        filters=PerformanceFilters(
            symbol=symbol,
            regime=regime,
            score_min=score_min,
            score_max=score_max,
            since=since,
            until=until,
        ),
    )
