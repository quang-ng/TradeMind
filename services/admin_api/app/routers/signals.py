import uuid

from common.db.models import Signal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..deps import get_db_session
from ..schemas import SignalDetailOut, SignalOut

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/signals", response_model=list[SignalOut])
async def list_signals(
    symbol: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
) -> list[Signal]:
    """PROJECT.md Section 11 `GET /signals?symbol=&limit=`."""
    stmt = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
    if symbol:
        stmt = stmt.where(Signal.symbol == symbol)
    return list((await session.execute(stmt)).scalars().all())


@router.get("/signals/{signal_id}", response_model=SignalDetailOut)
async def get_signal(
    signal_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> Signal:
    """PROJECT.md Section 11 `GET /signals/{id}` — includes the raw LLM
    response, unlike the list view."""
    signal = await session.get(Signal, signal_id)
    if signal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="signal not found")
    return signal
