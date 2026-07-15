from common.db.models import RiskDecision, Signal
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..deps import get_db_session
from ..schemas import RiskDecisionOut

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/decisions", response_model=list[RiskDecisionOut])
async def list_decisions(
    symbol: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
) -> list[RiskDecision]:
    """PROJECT.md Section 11 `GET /decisions?symbol=&limit=`. `RiskDecision`
    (Section 7.2) has no `symbol` column of its own — filtering by symbol
    joins through the parent `Signal`."""
    stmt = select(RiskDecision).order_by(RiskDecision.created_at.desc()).limit(limit)
    if symbol:
        stmt = stmt.join(Signal, RiskDecision.signal_id == Signal.id).where(Signal.symbol == symbol)
    return list((await session.execute(stmt)).scalars().all())
