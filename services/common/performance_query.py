"""Positive-expectancy plan M3 — the ORM-aware half of the Performance
Engine.

`performance.py` is deliberately free of any SQLAlchemy import so the same
`compute_*` / `summarize` math runs over a `scripts/backtest` CSV row just
as well as a live `Position` (Section 7). This module is the one place that
knows how to turn the production `positions` table into the
`ClosedTradeMetrics` rows that math consumes, shared by the live endpoint
(`admin_api/routers/performance.py`) and the scheduled snapshot job
(`scheduler/app/jobs.py`).
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.db.models import Position
from common.enums import PositionStatus
from common.performance import ClosedTradeMetrics


async def load_closed_trade_metrics(
    session: AsyncSession,
    *,
    symbol: str | None = None,
    regime: str | None = None,
    score_min: int | None = None,
    score_max: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[ClosedTradeMetrics]:
    """Every closed position matching the filters, oldest close first, as
    the minimal shape the Performance Engine needs.

    A position with no `closed_at` or no `pnl_usdt` is skipped even if its
    status somehow reads CLOSED — those two fields are what makes a row a
    realized trade, and every `compute_*` function assumes both are present.
    """
    query = select(Position).where(Position.status == PositionStatus.CLOSED.value)
    if symbol is not None:
        query = query.where(Position.symbol == symbol)
    if regime is not None:
        query = query.where(Position.market_regime == regime)
    if score_min is not None:
        query = query.where(Position.trade_score >= score_min)
    if score_max is not None:
        query = query.where(Position.trade_score <= score_max)
    if since is not None:
        query = query.where(Position.closed_at >= since)
    if until is not None:
        query = query.where(Position.closed_at <= until)
    query = query.order_by(Position.closed_at.asc())

    rows = (await session.execute(query)).scalars().all()
    return _to_metrics(rows)


def _to_metrics(rows: Sequence[Position]) -> list[ClosedTradeMetrics]:
    return [
        ClosedTradeMetrics(
            pnl_usdt=Decimal(row.pnl_usdt),
            r_multiple=None if row.r_multiple is None else Decimal(row.r_multiple),
            fees_usdt=None if row.fees_usdt is None else Decimal(row.fees_usdt),
            closed_at=row.closed_at,
        )
        for row in rows
        if row.closed_at is not None and row.pnl_usdt is not None
    ]
