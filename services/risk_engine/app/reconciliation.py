import asyncio
import logging
from datetime import datetime, timedelta, timezone

from common.config import FreqtradeSettings
from common.db.models import AuditEvent, Order, Position
from common.db.session import get_session_factory
from common.enums import AuditEventType, OrderSide, OrderStatus, PositionStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .freqtrade_client import FreqtradeClient, FreqtradeUnavailable
from .schemas import FreqtradeTrade

logger = logging.getLogger(__name__)


async def reconcile_submitted_orders(
    session: AsyncSession,
    freqtrade_client: FreqtradeClient,
    *,
    now: datetime,
    stale_after: timedelta,
) -> int:
    """Reconcile stale SUBMITTED orders against Freqtrade's trade record.

    Missing or ambiguous remote state is never guessed. It produces one
    auditable operator alert and leaves the order SUBMITTED for later review.
    """
    cutoff = now - stale_after
    orders = (
        await session.execute(
            select(Order).where(
                Order.status == OrderStatus.SUBMITTED.value,
                Order.created_at <= cutoff,
            )
        )
    ).scalars().all()

    reconciled = 0
    for order in orders:
        if order.freqtrade_trade_id is None:
            await _alert_once(session, order, "missing_freqtrade_trade_id")
            continue
        try:
            trade = await freqtrade_client.get_trade(trade_id=order.freqtrade_trade_id)
        except FreqtradeUnavailable as exc:
            logger.warning(
                "order_reconciliation_lookup_failed",
                extra={
                    "trace_id": str(order.trace_id),
                    "order_id": str(order.id),
                    "error": str(exc),
                },
            )
            await _alert_once(session, order, "freqtrade_lookup_failed")
            continue

        if trade.pair != order.symbol:
            logger.error(
                "order_reconciliation_pair_mismatch",
                extra={
                    "trace_id": str(order.trace_id),
                    "order_id": str(order.id),
                    "trade_id": trade.trade_id,
                    "order_symbol": order.symbol,
                    "trade_pair": trade.pair,
                },
            )
            await _alert_once(session, order, "freqtrade_pair_mismatch")
            continue

        if order.side == OrderSide.BUY.value and trade.is_open:
            if await _reconcile_open_entry(session, order, trade):
                reconciled += 1
        elif order.side == OrderSide.SELL.value and not trade.is_open:
            if await _reconcile_closed_exit(session, order, trade, now=now):
                reconciled += 1
        else:
            await _alert_once(session, order, "remote_state_ambiguous")

    await session.commit()
    return reconciled


async def _reconcile_open_entry(
    session: AsyncSession, order: Order, trade: FreqtradeTrade
) -> bool:
    if trade.open_rate is None or trade.amount is None:
        await _alert_once(session, order, "entry_fill_fields_missing")
        return False

    position = (
        await session.execute(select(Position).where(Position.entry_order_id == order.id))
    ).scalars().first()
    order.status = OrderStatus.FILLED.value
    order.filled_amount = trade.amount
    order.avg_price = trade.open_rate
    if position is None:
        session.add(
            Position(
                symbol=order.symbol,
                status=PositionStatus.OPEN.value,
                entry_order_id=order.id,
                entry_price=trade.open_rate,
                amount=trade.amount,
                opened_at=trade.open_date or datetime.now(timezone.utc),
            )
        )
    session.add(
        AuditEvent(
            trace_id=order.trace_id,
            event_type=AuditEventType.ORDER_FILLED.value,
            payload={
                "order_id": str(order.id),
                "trade_id": trade.trade_id,
                "pair": order.symbol,
                "side": OrderSide.BUY.value,
                "source": "reconciliation",
            },
        )
    )
    session.add(
        AuditEvent(
            trace_id=order.trace_id,
            event_type=AuditEventType.POSITION_OPENED.value,
            payload={
                "order_id": str(order.id),
                "trade_id": trade.trade_id,
                "pair": order.symbol,
                "source": "reconciliation",
            },
        )
    )
    return True


async def _reconcile_closed_exit(
    session: AsyncSession,
    order: Order,
    trade: FreqtradeTrade,
    *,
    now: datetime,
) -> bool:
    if trade.close_rate is None or trade.amount is None:
        await _alert_once(session, order, "exit_fill_fields_missing")
        return False
    position = (
        await session.execute(select(Position).where(Position.exit_order_id == order.id))
    ).scalars().first()
    if position is None:
        await _alert_once(session, order, "open_position_missing")
        return False

    order.status = OrderStatus.FILLED.value
    order.filled_amount = trade.amount
    order.avg_price = trade.close_rate
    position.status = PositionStatus.CLOSED.value
    position.exit_price = trade.close_rate
    position.pnl_usdt = trade.profit_abs
    position.pnl_pct = trade.profit_ratio
    position.closed_at = trade.close_date or now
    session.add(
        AuditEvent(
            trace_id=order.trace_id,
            event_type=AuditEventType.ORDER_FILLED.value,
            payload={
                "order_id": str(order.id),
                "trade_id": trade.trade_id,
                "pair": order.symbol,
                "side": OrderSide.SELL.value,
                "source": "reconciliation",
            },
        )
    )
    session.add(
        AuditEvent(
            trace_id=order.trace_id,
            event_type=AuditEventType.POSITION_CLOSED.value,
            payload={
                "order_id": str(order.id),
                "trade_id": trade.trade_id,
                "pair": order.symbol,
                "pnl_usdt": str(trade.profit_abs) if trade.profit_abs is not None else None,
                "source": "reconciliation",
            },
        )
    )
    return True


async def _alert_once(session: AsyncSession, order: Order, reason: str) -> None:
    existing = (
        await session.execute(
            select(AuditEvent.id).where(
                AuditEvent.trace_id == order.trace_id,
                AuditEvent.event_type == AuditEventType.RECONCILIATION_REQUIRED.value,
            )
        )
    ).first()
    if existing is not None:
        return
    session.add(
        AuditEvent(
            trace_id=order.trace_id,
            event_type=AuditEventType.RECONCILIATION_REQUIRED.value,
            payload={"order_id": str(order.id), "symbol": order.symbol, "reason": reason},
        )
    )


async def run_reconciliation_loop() -> None:
    settings = FreqtradeSettings()
    session_factory = get_session_factory()
    client = FreqtradeClient(settings)
    logger.info(
        "order_reconciliation_started",
        extra={
            "interval_seconds": settings.reconciliation_interval_seconds,
            "order_age_minutes": settings.reconciliation_order_age_minutes,
        },
    )
    try:
        while True:
            try:
                async with session_factory() as session:
                    reconciled = await reconcile_submitted_orders(
                        session,
                        client,
                        now=datetime.now(timezone.utc),
                        stale_after=timedelta(
                            minutes=settings.reconciliation_order_age_minutes
                        ),
                    )
                    if reconciled:
                        logger.info("orders_reconciled", extra={"count": reconciled})
            except Exception:
                logger.exception("order_reconciliation_cycle_failed")
            await asyncio.sleep(settings.reconciliation_interval_seconds)
    finally:
        await client.aclose()
