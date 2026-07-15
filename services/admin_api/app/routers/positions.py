from common.db.models import Position
from common.enums import PositionStatus
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..deps import get_db_session
from ..schemas import PositionOut

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/positions", response_model=list[PositionOut])
async def list_positions(
    status_filter: str | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_db_session),
) -> list[Position]:
    """PROJECT.md Section 11 `GET /positions?status=open|closed`."""
    stmt = select(Position).order_by(Position.opened_at.desc())
    if status_filter is not None:
        normalized = status_filter.upper()
        if normalized not in (PositionStatus.OPEN.value, PositionStatus.CLOSED.value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="status must be 'open' or 'closed'",
            )
        stmt = stmt.where(Position.status == normalized)
    return list((await session.execute(stmt)).scalars().all())
