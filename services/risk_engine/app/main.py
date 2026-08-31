import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import redis.asyncio as redis
from common import kill_switch, redis_keys
from common.config import FreqtradeSettings, RedisSettings, RiskConfig
from common.db.models import AuditEvent, Order, Position, RiskDecision, Signal
from common.db.session import get_session_factory
from common.enums import (
    Action,
    AuditEventType,
    OrderSide,
    OrderStatus,
    PositionStatus,
    RejectionReason,
)
from common.logging import configure_json_logging
from common.risk_config_store import load_effective_risk_config
from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .account_state import load_account_state
from .balance_monitor import publish_balance_snapshot, run_balance_refresh_loop
from .evaluator import RiskDecisionResult, evaluate
from .exit_evaluator import ExitDecisionResult, evaluate_exit
from .expectancy_state import load_expectancy_state
from .freqtrade_client import FreqtradeClient, FreqtradeUnavailable
from .reconciliation import run_reconciliation_loop
from .rules.expectancy_filter import ExpectancyCheck
from .schemas import ExpectancyView, SignalView

configure_json_logging()
logger = logging.getLogger(__name__)


async def process_signal(
    session: AsyncSession,
    redis_client: redis.Redis,
    signal_id: str,
    config: RiskConfig,
    freqtrade_client: FreqtradeClient,
) -> None:
    """PROJECT.md Section 5.1 steps 5-10: read the signal + account state,
    run the pure rule evaluation, persist `RiskDecision` and an
    `AuditEvent`, then (if approved) submit the order to Freqtrade and
    persist `Order` (AGENTS.md Section 7 / PROJECT.md Section 14 rule 7).

    `BUY`/`HOLD` go through the Section 9.1 entry pipeline (`evaluate`);
    `SELL` goes through the lighter exit pipeline (`evaluate_exit`) — see
    `exit_evaluator.py` for why these are deliberately different gates.
    """
    signal_row = await session.get(Signal, uuid.UUID(signal_id))
    if signal_row is None:
        logger.warning("signal_not_found", extra={"signal_id": signal_id})
        return

    try:
        balance = await publish_balance_snapshot(redis_client, freqtrade_client)
    except FreqtradeUnavailable as exc:
        await _record_balance_unavailable(session, signal_row, exc)
        return

    dup_key = redis_keys.decision_idempotency(str(signal_row.id))
    is_duplicate = not await redis_client.set(
        dup_key, "1", nx=True, ex=redis_keys.DECISION_IDEMPOTENCY_TTL_SECONDS
    )
    account = await load_account_state(session, balance=balance)
    signal_view = SignalView(
        id=str(signal_row.id),
        symbol=signal_row.symbol,
        action=signal_row.action,
        confidence=Decimal(signal_row.confidence),
        candle_ts=signal_row.candle_ts,
        price=Decimal(signal_row.price),
        atr_14=Decimal(signal_row.atr_14),
        setup_regime=signal_row.setup_regime,
        trade_score=signal_row.trade_score,
    )

    if signal_view.action == Action.SELL:
        await _handle_exit_signal(
            session, freqtrade_client, config, signal_row, signal_view, account, is_duplicate
        )
        return

    # Positive-expectancy plan M5: the setup cohort's historical expectancy,
    # pre-fetched here (I/O) so `evaluate()` stays pure — same pattern as
    # `load_account_state` above. Exit signals never reach the entry rule
    # set, so this is entry-only.
    expectancy = await load_expectancy_state(
        session,
        market_regime=signal_view.setup_regime,
        trade_score=signal_view.trade_score,
    )
    await _handle_entry_signal(
        session, redis_client, freqtrade_client, config, signal_row, signal_view, account,
        is_duplicate, expectancy,
    )


async def _record_balance_unavailable(
    session: AsyncSession, signal_row: Signal, error: FreqtradeUnavailable
) -> None:
    """Persist an auditable rejection without inventing an equity snapshot."""
    reason = RejectionReason.FREQTRADE_BALANCE_UNAVAILABLE
    session.add(
        RiskDecision(
            trace_id=signal_row.trace_id,
            signal_id=signal_row.id,
            approved=False,
            rejection_reason=reason.value,
            equity_snapshot_usdt=None,
        )
    )
    await _write_decision_audit_event(session, signal_row, False, reason)
    await session.commit()
    logger.error(
        "risk_decision_blocked_balance_unavailable",
        extra={
            "trace_id": str(signal_row.trace_id),
            "signal_id": str(signal_row.id),
            "error": str(error),
        },
    )


async def _handle_entry_signal(
    session: AsyncSession,
    redis_client: redis.Redis,
    freqtrade_client: FreqtradeClient,
    config: RiskConfig,
    signal_row: Signal,
    signal_view: SignalView,
    account,
    is_duplicate: bool,
    expectancy: ExpectancyView,
) -> None:
    killswitch_enabled = await kill_switch.is_enabled(session)
    try:
        result = evaluate(
            signal=signal_view,
            account=account,
            config=config,
            now=datetime.now(timezone.utc),
            killswitch_enabled=killswitch_enabled,
            is_duplicate_decision=is_duplicate,
            expectancy=expectancy,
        )
    except Exception:
        # PROJECT.md Section 9.4: never allowed to propagate into an approval.
        logger.exception(
            "risk_rule_evaluation_failed",
            extra={"trace_id": str(signal_row.trace_id), "signal_id": str(signal_row.id)},
        )
        result = RiskDecisionResult(
            approved=False,
            rejection_reason=RejectionReason.INTERNAL_ERROR,
            equity_snapshot_usdt=account.equity_usdt,
        )

    if result.auto_trip_killswitch:
        await kill_switch.enable(
            session,
            redis_client,
            reason=result.rejection_reason.value if result.rejection_reason else "AUTO_TRIP",
            updated_by="SYSTEM",
            trace_id=signal_row.trace_id,
        )

    decision = RiskDecision(
        trace_id=signal_row.trace_id,
        signal_id=signal_row.id,
        approved=result.approved,
        rejection_reason=result.rejection_reason.value if result.rejection_reason else None,
        position_size_usdt=result.position_size_usdt,
        position_size_base=result.position_size_base,
        stop_loss_price=result.stop_loss_price,
        stop_distance_pct=result.stop_distance_pct,
        equity_snapshot_usdt=result.equity_snapshot_usdt,
        risk_pct_applied=result.risk_pct_applied,
        nominal_risk_amount_usdt=result.nominal_risk_amount_usdt,
        actual_risk_usdt=result.actual_risk_usdt,
    )
    session.add(decision)
    await session.flush()
    await _write_decision_audit_event(
        session,
        signal_row,
        result.approved,
        result.rejection_reason,
        nominal_risk_amount_usdt=result.nominal_risk_amount_usdt,
        actual_risk_usdt=result.actual_risk_usdt,
        stop_distance_pct=result.stop_distance_pct,
        expectancy_check=result.expectancy_check,
    )

    if result.approved:
        assert result.position_size_usdt is not None and result.position_size_base is not None
        await _submit_entry_order(
            session, freqtrade_client, config, signal_row, decision.id, result.position_size_usdt,
            result.position_size_base, result.stop_loss_price,
        )

    await session.commit()
    logger.info(
        "risk_decision_recorded",
        extra={
            "trace_id": str(signal_row.trace_id),
            "signal_id": str(signal_row.id),
            "action": "BUY",
            "approved": result.approved,
            "rejection_reason": result.rejection_reason.value if result.rejection_reason else None,
        },
    )


async def _handle_exit_signal(
    session: AsyncSession,
    freqtrade_client: FreqtradeClient,
    config: RiskConfig,
    signal_row: Signal,
    signal_view: SignalView,
    account,
    is_duplicate: bool,
) -> None:
    try:
        result = evaluate_exit(
            signal=signal_view,
            account=account,
            config=config,
            now=datetime.now(timezone.utc),
            is_duplicate_decision=is_duplicate,
        )
    except Exception:
        logger.exception(
            "risk_exit_evaluation_failed",
            extra={"trace_id": str(signal_row.trace_id), "signal_id": str(signal_row.id)},
        )
        result = ExitDecisionResult(
            approved=False,
            rejection_reason=RejectionReason.INTERNAL_ERROR,
            equity_snapshot_usdt=account.equity_usdt,
        )

    decision = RiskDecision(
        trace_id=signal_row.trace_id,
        signal_id=signal_row.id,
        approved=result.approved,
        rejection_reason=result.rejection_reason.value if result.rejection_reason else None,
        equity_snapshot_usdt=result.equity_snapshot_usdt,
    )
    session.add(decision)
    await session.flush()
    await _write_decision_audit_event(session, signal_row, result.approved, result.rejection_reason)

    if result.approved:
        await _submit_exit_order(session, freqtrade_client, config, signal_row, decision.id)

    await session.commit()
    logger.info(
        "risk_decision_recorded",
        extra={
            "trace_id": str(signal_row.trace_id),
            "signal_id": str(signal_row.id),
            "action": "SELL",
            "approved": result.approved,
            "rejection_reason": result.rejection_reason.value if result.rejection_reason else None,
        },
    )


async def _write_decision_audit_event(
    session: AsyncSession,
    signal_row: Signal,
    approved: bool,
    rejection_reason: RejectionReason | None,
    *,
    nominal_risk_amount_usdt: Decimal | None = None,
    actual_risk_usdt: Decimal | None = None,
    stop_distance_pct: Decimal | None = None,
    expectancy_check: ExpectancyCheck | None = None,
) -> None:
    event_type = AuditEventType.RISK_APPROVED if approved else AuditEventType.RISK_REJECTED
    payload = {
        "signal_id": str(signal_row.id),
        "approved": approved,
        "rejection_reason": rejection_reason.value if rejection_reason else None,
    }
    # Positive-expectancy plan M1: the sizing math that produced an approved
    # decision is audit-visible from day one, not just persisted on the row —
    # SELL/exit decisions (exit_evaluator.py has no sizing candidate) leave
    # these None, matching the nullable RiskDecision columns.
    if approved:
        payload["nominal_risk_amount_usdt"] = (
            str(nominal_risk_amount_usdt) if nominal_risk_amount_usdt is not None else None
        )
        payload["actual_risk_usdt"] = (
            str(actual_risk_usdt) if actual_risk_usdt is not None else None
        )
        payload["stop_distance_pct"] = (
            str(stop_distance_pct) if stop_distance_pct is not None else None
        )
    # Positive-expectancy plan M5 (D4): on *every* evaluated entry — approved
    # or rejected, filter on or off — record what the Historical Expectancy
    # Filter concluded, so shadow-mode data is reconstructable per-trace from
    # day one. Absent only on the exit path and the INTERNAL_ERROR fallback.
    if expectancy_check is not None:
        payload["expectancy_check"] = expectancy_check.as_payload()
    session.add(
        AuditEvent(
            trace_id=signal_row.trace_id,
            event_type=event_type.value,
            payload=payload,
        )
    )


async def _submit_entry_order(
    session: AsyncSession,
    freqtrade_client: FreqtradeClient,
    config: RiskConfig,
    signal_row: Signal,
    risk_decision_id: uuid.UUID,
    position_size_usdt: Decimal,
    position_size_base: Decimal,
    stop_loss_price: Decimal | None,
) -> None:
    """PROJECT.md Section 5.1 step 8-9. Freqtrade unreachable -> `Order`
    persisted as `FAILED` (Section 9.4), never blocks the already-persisted
    `RiskDecision(approved=true)`."""
    # Carry the distance rather than a pre-entry absolute price. Freqtrade
    # chooses the live entry rate when forceenter runs, which can differ
    # materially from the earlier signal price. The strategy derives the
    # absolute stop from the authoritative Trade.open_rate after fill.
    stop_distance_pct = (
        Decimal("1") - (stop_loss_price / signal_row.price)
        if stop_loss_price is not None and signal_row.price > 0
        else None
    )
    entry_tag = f"slpct:{stop_distance_pct}" if stop_distance_pct is not None else None
    try:
        response = await freqtrade_client.forceenter(
            pair=signal_row.symbol, stake_amount=position_size_usdt, entry_tag=entry_tag
        )
        freqtrade_trade_id = response.get("trade_id") or response.get("id")
        status = OrderStatus.SUBMITTED
        event_type = AuditEventType.ORDER_SUBMITTED
    except FreqtradeUnavailable as exc:
        logger.error(
            "freqtrade_forceenter_failed",
            extra={
                "trace_id": str(signal_row.trace_id),
                "symbol": signal_row.symbol,
                "error": str(exc),
            },
        )
        freqtrade_trade_id = None
        status = OrderStatus.FAILED
        event_type = AuditEventType.ORDER_FAILED

    order = Order(
        trace_id=signal_row.trace_id,
        risk_decision_id=risk_decision_id,
        freqtrade_trade_id=freqtrade_trade_id,
        symbol=signal_row.symbol,
        side=OrderSide.BUY.value,
        status=status.value,
        requested_amount=position_size_base,
        dry_run=config.dry_run,
    )
    session.add(order)
    session.add(
        AuditEvent(
            trace_id=signal_row.trace_id,
            event_type=event_type.value,
            payload={"symbol": signal_row.symbol, "side": "BUY", "status": status.value},
        )
    )


async def _submit_exit_order(
    session: AsyncSession,
    freqtrade_client: FreqtradeClient,
    config: RiskConfig,
    signal_row: Signal,
    risk_decision_id: uuid.UUID,
) -> None:
    position = (
        await session.execute(
            select(Position).where(
                Position.symbol == signal_row.symbol, Position.status == PositionStatus.OPEN.value
            )
        )
    ).scalars().first()
    if position is None:
        # Evaluated as approved but the position closed between evaluation
        # and here (e.g. a concurrent close) -> fail closed, no order.
        logger.warning(
            "exit_approved_but_no_open_position",
            extra={"trace_id": str(signal_row.trace_id), "symbol": signal_row.symbol},
        )
        return
    entry_order = await session.get(Order, position.entry_order_id)
    if entry_order is None or entry_order.freqtrade_trade_id is None:
        logger.error(
            "exit_approved_but_entry_trade_id_missing",
            extra={"trace_id": str(signal_row.trace_id), "symbol": signal_row.symbol},
        )
        return

    try:
        trade = await freqtrade_client.get_trade(trade_id=entry_order.freqtrade_trade_id)
        if trade.pair != signal_row.symbol or not trade.is_open:
            raise FreqtradeUnavailable(
                "entry trade does not match the open position "
                f"(expected_pair={signal_row.symbol}, actual_pair={trade.pair}, "
                f"is_open={trade.is_open})"
            )
        await freqtrade_client.forceexit(trade_id=entry_order.freqtrade_trade_id)
        status = OrderStatus.SUBMITTED
        event_type = AuditEventType.ORDER_SUBMITTED
    except FreqtradeUnavailable as exc:
        logger.error(
            "freqtrade_forceexit_failed",
            extra={
                "trace_id": str(signal_row.trace_id),
                "symbol": signal_row.symbol,
                "error": str(exc),
            },
        )
        status = OrderStatus.FAILED
        event_type = AuditEventType.ORDER_FAILED

    order = Order(
        trace_id=signal_row.trace_id,
        risk_decision_id=risk_decision_id,
        freqtrade_trade_id=entry_order.freqtrade_trade_id,
        symbol=signal_row.symbol,
        side=OrderSide.SELL.value,
        status=status.value,
        requested_amount=position.amount,
        dry_run=config.dry_run,
    )
    session.add(order)
    await session.flush()
    if status == OrderStatus.SUBMITTED:
        position.exit_order_id = order.id
    session.add(
        AuditEvent(
            trace_id=signal_row.trace_id,
            event_type=event_type.value,
            payload={"symbol": signal_row.symbol, "side": "SELL", "status": status.value},
        )
    )


async def _ensure_consumer_group(redis_client: redis.Redis) -> None:
    try:
        await redis_client.xgroup_create(
            redis_keys.SIGNALS_PENDING_STREAM,
            redis_keys.SIGNALS_PENDING_CONSUMER_GROUP,
            id="0",
            mkstream=True,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def _handle_message(
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: redis.Redis,
    freqtrade_client: FreqtradeClient,
    message_id: str,
    fields: dict,
) -> None:
    signal_id = fields.get("signal_id")
    if not signal_id:
        logger.warning("message_missing_signal_id", extra={"message_id": message_id})
        await redis_client.xack(
            redis_keys.SIGNALS_PENDING_STREAM, redis_keys.SIGNALS_PENDING_CONSUMER_GROUP, message_id
        )
        return
    try:
        async with session_factory() as session:
            # Loaded fresh per message (not once at startup) so a `PATCH
            # /config` (PROJECT.md Section 11) takes effect on the next
            # signal without a service restart.
            config = await load_effective_risk_config(session)
            await process_signal(session, redis_client, signal_id, config, freqtrade_client)
        await redis_client.xack(
            redis_keys.SIGNALS_PENDING_STREAM, redis_keys.SIGNALS_PENDING_CONSUMER_GROUP, message_id
        )
    except Exception:
        # PostgreSQL/Redis unavailable -> fail closed, no ack, message is
        # redelivered to the consumer group once the dependency recovers
        # (PROJECT.md Section 9.4).
        logger.exception(
            "risk_engine_message_processing_failed",
            extra={"signal_id": signal_id, "message_id": message_id},
        )


async def run_consumer() -> None:
    session_factory = get_session_factory()
    # socket_timeout must exceed XREADGROUP's block= wait below, or the
    # client's own read times out before Redis's blocking period elapses
    # (a well-known redis-py gotcha for blocking commands).
    redis_client = redis.from_url(
        RedisSettings().redis_url, decode_responses=True, socket_timeout=10.0
    )
    freqtrade_client = FreqtradeClient(FreqtradeSettings())

    await _ensure_consumer_group(redis_client)
    consumer_name = f"risk_engine-{uuid.uuid4().hex[:8]}"

    logger.info("risk_engine_consumer_started", extra={"consumer_name": consumer_name})
    while True:
        response = await redis_client.xreadgroup(
            groupname=redis_keys.SIGNALS_PENDING_CONSUMER_GROUP,
            consumername=consumer_name,
            streams={redis_keys.SIGNALS_PENDING_STREAM: ">"},
            count=10,
            block=5000,
        )
        for _, messages in response or []:
            for message_id, fields in messages:
                await _handle_message(
                    session_factory, redis_client, freqtrade_client, message_id, fields,
                )


if __name__ == "__main__":
    async def run_service() -> None:
        await asyncio.gather(
            run_consumer(),
            run_reconciliation_loop(),
            run_balance_refresh_loop(),
        )

    asyncio.run(run_service())
