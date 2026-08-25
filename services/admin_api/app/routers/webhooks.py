import hmac
import logging
from datetime import datetime, timezone
from decimal import Decimal

from common.config import WebhookSettings
from common.db.models import AuditEvent, Order, Position, RiskDecision, Signal
from common.enums import AuditEventType, OrderSide, OrderStatus, PositionStatus
from common.risk_config_store import load_effective_risk_config
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

    entry_signal = await _load_entry_signal(session, order)

    position = Position(
        symbol=payload.pair,
        status=PositionStatus.OPEN.value,
        entry_order_id=order.id,
        entry_price=payload.open_rate,
        amount=payload.amount,
        # Positive-expectancy plan M2 — denormalized from the entry Signal
        # (same precedent as entry_price/amount, Section 3 M2). `None` when
        # no linked Signal is found (e.g. legacy order predating M2).
        market_regime=entry_signal.setup_regime if entry_signal is not None else None,
        trade_score=entry_signal.trade_score if entry_signal is not None else None,
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


async def _load_entry_signal(session: AsyncSession, entry_order: Order) -> Signal | None:
    """`entry_order.risk_decision_id -> RiskDecision.signal_id -> Signal`
    (positive-expectancy plan M2) — the same linkage `_load_actual_risk_usdt`
    below walks for R, reused here to denormalize `setup_regime`/
    `trade_score` onto the new `Position` row at open."""
    risk_decision = await session.get(RiskDecision, entry_order.risk_decision_id)
    if risk_decision is None:
        return None
    return await session.get(Signal, risk_decision.signal_id)


async def _load_actual_risk_usdt(session: AsyncSession, position: Position) -> Decimal | None:
    """The R-multiple denominator (positive-expectancy plan D1) — looked up
    via the entry order's linked `RiskDecision`, not recomputed here.
    `actual_risk_usdt` already captures the exact post-clamp sizing math
    that produced this trade (`risk_engine/app/sizing.py`). `None` for
    legacy positions whose entry decision predates this column."""
    entry_order = await session.get(Order, position.entry_order_id)
    if entry_order is None:
        return None
    risk_decision = await session.get(RiskDecision, entry_order.risk_decision_id)
    if risk_decision is None:
        return None
    return risk_decision.actual_risk_usdt


async def _estimate_fees_usdt(
    session: AsyncSession, position: Position, payload: FreqtradeWebhookPayload
) -> Decimal | None:
    """Round-trip fee estimate (entry notional + exit notional, both at
    `RiskConfig.estimated_fee_pct`) — the only fee figure available today,
    since the Freqtrade webhook payload carries no real per-trade fee."""
    if payload.close_rate is None or payload.amount is None:
        return None
    config = await load_effective_risk_config(session)
    entry_notional = position.entry_price * position.amount
    exit_notional = payload.close_rate * payload.amount
    return config.estimated_fee_pct * (entry_notional + exit_notional)


async def _handle_exit_fill(session: AsyncSession, payload: FreqtradeWebhookPayload) -> None:
    order = await _find_order(
        session, trade_id=payload.trade_id, pair=payload.pair, side=OrderSide.SELL.value
    )
    if payload.close_rate is None or payload.amount is None:
        logger.warning(
            "exit_fill_no_matching_order",
            extra={"trade_id": payload.trade_id, "pair": payload.pair},
        )
        return

    # ROI and stop-loss are Freqtrade-owned safety exits, so no TradeMind
    # SELL order exists before their webhook arrives. Anchor a synthetic,
    # fully-audited exit order to the entry decision/trace in that case.
    if order is None:
        match = (
            await session.execute(
                select(Position, Order)
                .join(Order, Position.entry_order_id == Order.id)
                .where(
                    Position.symbol == payload.pair,
                    Position.status == PositionStatus.OPEN.value,
                    Order.freqtrade_trade_id == payload.trade_id,
                    Order.side == OrderSide.BUY.value,
                )
            )
        ).first()
        if match is None:
            logger.warning(
                "exit_fill_no_matching_order",
                extra={"trade_id": payload.trade_id, "pair": payload.pair},
            )
            return
        position, entry_order = match
        order = Order(
            trace_id=entry_order.trace_id,
            risk_decision_id=entry_order.risk_decision_id,
            freqtrade_trade_id=payload.trade_id,
            symbol=payload.pair,
            side=OrderSide.SELL.value,
            status=OrderStatus.FILLED.value,
            requested_amount=position.amount,
            filled_amount=payload.amount,
            avg_price=payload.close_rate,
            dry_run=entry_order.dry_run,
        )
        session.add(order)
        await session.flush()

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

    actual_risk_usdt = await _load_actual_risk_usdt(session, position)
    r_multiple = (
        payload.profit_amount / actual_risk_usdt
        if actual_risk_usdt and payload.profit_amount is not None
        else None
    )
    fees_usdt = await _estimate_fees_usdt(session, position, payload)

    position.status = PositionStatus.CLOSED
    position.exit_order_id = order.id
    position.exit_price = payload.close_rate
    position.pnl_usdt = payload.profit_amount
    position.pnl_pct = payload.profit_ratio
    position.closed_at = _parse_freqtrade_datetime(payload.close_date) or datetime.now(timezone.utc)
    position.exit_reason = payload.exit_reason
    position.r_multiple = r_multiple
    position.fees_usdt = fees_usdt
    # Positive-expectancy plan M1: the Freqtrade webhook payload carries no
    # real per-trade fee figure today (Section 1 gap analysis), so this is
    # always a config-estimated value until a genuine source is exposed.
    position.fees_estimated = True

    session.add(
        AuditEvent(
            trace_id=order.trace_id,
            event_type=AuditEventType.ORDER_FILLED.value,
            payload={
                "trade_id": payload.trade_id,
                "pair": payload.pair,
                "side": "SELL",
                "source": "freqtrade_webhook",
                "exit_reason": payload.exit_reason,
            },
        )
    )
    pnl_usdt = str(payload.profit_amount) if payload.profit_amount is not None else None
    session.add(
        AuditEvent(
            trace_id=order.trace_id,
            event_type=AuditEventType.POSITION_CLOSED.value,
            payload={
                "trade_id": payload.trade_id,
                "pair": payload.pair,
                "pnl_usdt": pnl_usdt,
                "source": "freqtrade_webhook",
                "exit_reason": payload.exit_reason,
                "r_multiple": str(r_multiple) if r_multiple is not None else None,
                "fees_usdt": str(fees_usdt) if fees_usdt is not None else None,
                "fees_estimated": True,
            },
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
