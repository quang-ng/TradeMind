import uuid

from common.db.models import Order
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..deps import get_db_session
from ..schemas import OrderOut

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/orders", response_model=list[OrderOut])
async def list_orders(
    symbol: str | None = None,
    order_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
) -> list[Order]:
    """PROJECT.md Section 11 `GET /orders?symbol=&status=&limit=` — all
    order logs across all pairs."""
    stmt = select(Order).order_by(Order.created_at.desc()).limit(limit)
    if symbol:
        stmt = stmt.where(Order.symbol == symbol)
    if order_status:
        stmt = stmt.where(Order.status == order_status.upper())
    return list((await session.execute(stmt)).scalars().all())


@router.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(order_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)) -> Order:
    """PROJECT.md Section 11 `GET /orders/{id}`."""
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
    return order
