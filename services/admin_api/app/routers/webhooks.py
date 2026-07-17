import hmac
import logging
from datetime import datetime, timezone

from common.config import WebhookSettings
from common.db.models import AuditEvent, Order, Position
from common.enums import AuditEventType, OrderSide, OrderStatus, PositionStatus
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db_session
from ..schemas import FreqtradeWebhookPayload

logger = logging.getLogger(__name__)
router = APIRouter()


def get_webhook_settings() -> WebhookSettings:
    return WebhookSettings()


@router.post("/webhooks/freqtrade", status_code=status.HTTP_204_NO_CONTENT)
async def freqtrade_webhook(
    payload: FreqtradeWebhookPayload,
    session: AsyncSession = Depends(get_db_session),
    settings: WebhookSettings = Depends(get_webhook_settings),
) -> None:
    """PROJECT.md Section 11: authenticated by `WEBHOOK_SHARED_SECRET`, not
    the operator `ADMIN_API_KEY` (Section 14 rule 9: secrets stay scoped to
    the boundary they authenticate)."""
    if not settings.webhook_shared_secret or not hmac.compare_digest(
        payload.secret, settings.webhook_shared_secret
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid secret")

    handler = _EVENT_HANDLERS.get(payload.event)
    if handler is None:
        logger.info(
            "webhook_event_ignored",
            extra={"event": payload.event, "trade_id": payload.trade_id},
        )
        return

    await handler(session, payload)
    await session.commit()


async def _find_order(
    session: AsyncSession, *, trade_id: int, pair: str, side: str
) -> Order | None:
    order = (
        await session.execute(
            select(Order)
            .where(
                Order.freqtrade_trade_id == trade_id,
                Order.symbol == pair,
                Order.side == side,
            )
            .order_by(Order.created_at.desc())
        )
    ).scalars().first()
    if order is not None:
        return order

    # The synchronous forceenter/forceexit response may not have carried a
    # trade_id (Freqtrade API response shape isn't guaranteed) — fall back
    # to the most recent unmatched SUBMITTED order for this pair/side and
    # backfill it from this webhook.
    order = (
        await session.execute(
            select(Order)
            .where(
                Order.symbol == pair,
                Order.side == side,
                Order.status == OrderStatus.SUBMITTED.value,
                Order.freqtrade_trade_id.is_(None),
            )
            .order_by(Order.created_at.desc())
        )
    ).scalars().first()
    if order is not None:
        order.freqtrade_trade_id = trade_id
    return order


def _parse_freqtrade_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        logger.warning("webhook_unparseable_datetime", extra={"value": value})
        return None


async def _handle_entry_fill(session: AsyncSession, payload: FreqtradeWebhookPayload) -> None:
    order = await _find_order(
        session, trade_id=payload.trade_id, pair=payload.pair, side=OrderSide.BUY.value
    )
    if order is None or payload.open_rate is None or payload.amount is None:
        logger.warning(
            "entry_fill_no_matching_order",
            extra={"trade_id": payload.trade_id, "pair": payload.pair},
        )
        return

    order.status = OrderStatus.FILLED
    order.filled_amount = payload.amount
    order.avg_price = payload.open_rate
    await session.flush()

    position = Position(
        symbol=payload.pair,
        status=PositionStatus.OPEN.value,
        entry_order_id=order.id,
        entry_price=payload.open_rate,
        amount=payload.amount,
    )
    opened_at = _parse_freqtrade_datetime(payload.open_date)
    if opened_at is not None:
        position.opened_at = opened_at
    session.add(position)

    session.add(
        AuditEvent(
            trace_id=order.trace_id,
            event_type=AuditEventType.ORDER_FILLED.value,
            payload={"trade_id": payload.trade_id, "pair": payload.pair, "side": "BUY"},
        )
    )
    session.add(
        AuditEvent(
            trace_id=order.trace_id,
            event_type=AuditEventType.POSITION_OPENED.value,
            payload={
                "trade_id": payload.trade_id,
                "pair": payload.pair,
                "entry_price": str(payload.open_rate),
                "amount": str(payload.amount),
            },
        )
    )


async def _handle_exit_fill(session: AsyncSession, payload: FreqtradeWebhookPayload) -> None:
    order = await _find_order(
        session, trade_id=payload.trade_id, pair=payload.pair, side=OrderSide.SELL.value
    )
    if order is None or payload.close_rate is None or payload.amount is None:
        logger.warning(
            "exit_fill_no_matching_order",
            extra={"trade_id": payload.trade_id, "pair": payload.pair},
        )
        return

    order.status = OrderStatus.FILLED
    order.filled_amount = payload.amount
    order.avg_price = payload.close_rate
    await session.flush()

    position = (
        await session.execute(select(Position).where(Position.exit_order_id == order.id))
    ).scalars().first()
    if position is None:
        position = (
            await session.execute(
                select(Position).where(
                    Position.symbol == payload.pair, Position.status == PositionStatus.OPEN.value
                )
            )
        ).scalars().first()
    if position is None:
        logger.error(
            "exit_fill_no_matching_position",
            extra={"trade_id": payload.trade_id, "pair": payload.pair},
        )
        return

    position.status = PositionStatus.CLOSED
    position.exit_order_id = order.id
    position.exit_price = payload.close_rate
    position.pnl_usdt = payload.profit_amount
    position.pnl_pct = payload.profit_ratio
    position.closed_at = _parse_freqtrade_datetime(payload.close_date) or datetime.now(timezone.utc)

    session.add(
        AuditEvent(
            trace_id=order.trace_id,
            event_type=AuditEventType.ORDER_FILLED.value,
            payload={"trade_id": payload.trade_id, "pair": payload.pair, "side": "SELL"},
        )
    )
    pnl_usdt = str(payload.profit_amount) if payload.profit_amount is not None else None
    session.add(
        AuditEvent(
            trace_id=order.trace_id,
            event_type=AuditEventType.POSITION_CLOSED.value,
            payload={"trade_id": payload.trade_id, "pair": payload.pair, "pnl_usdt": pnl_usdt},
        )
    )


async def _handle_entry_cancel(session: AsyncSession, payload: FreqtradeWebhookPayload) -> None:
    await _mark_cancelled(session, payload, side=OrderSide.BUY.value)


async def _handle_exit_cancel(session: AsyncSession, payload: FreqtradeWebhookPayload) -> None:
    await _mark_cancelled(session, payload, side=OrderSide.SELL.value)


async def _mark_cancelled(
    session: AsyncSession, payload: FreqtradeWebhookPayload, *, side: str
) -> None:
    order = await _find_order(session, trade_id=payload.trade_id, pair=payload.pair, side=side)
    if order is None:
        logger.warning(
            "cancel_no_matching_order", extra={"trade_id": payload.trade_id, "pair": payload.pair}
        )
        return
    order.status = OrderStatus.CANCELLED
    session.add(
        AuditEvent(
            trace_id=order.trace_id,
            event_type=AuditEventType.ORDER_CANCELLED.value,
            payload={"trade_id": payload.trade_id, "pair": payload.pair, "side": side},
        )
    )


_EVENT_HANDLERS = {
    "entry_fill": _handle_entry_fill,
    "exit_fill": _handle_exit_fill,
    "entry_cancel": _handle_entry_cancel,
    "exit_cancel": _handle_exit_cancel,
}
