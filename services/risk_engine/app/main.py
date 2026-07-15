import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import redis.asyncio as redis
from common import redis_keys
from common.config import AccountSettings, RedisSettings, RiskConfig
from common.db.models import AuditEvent, RiskDecision, Signal
from common.db.session import get_session_factory
from common.enums import AuditEventType, RejectionReason
from common.logging import configure_json_logging
from redis.exceptions import ResponseError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import kill_switch
from .account_state import load_account_state
from .evaluator import RiskDecisionResult, evaluate
from .schemas import SignalView

configure_json_logging()
logger = logging.getLogger(__name__)


async def process_signal(
    session: AsyncSession,
    redis_client: redis.Redis,
    signal_id: str,
    config: RiskConfig,
    account_settings: AccountSettings,
) -> None:
    """PROJECT.md Section 5.1 steps 5-7, 10 (order submission is Phase 3
    scope): read the signal + account state, run the pure rule evaluation,
    persist `RiskDecision` and an `AuditEvent` in the same transaction
    (AGENTS.md Section 7 / PROJECT.md Section 14 rule 7)."""
    signal_row = await session.get(Signal, uuid.UUID(signal_id))
    if signal_row is None:
        logger.warning("signal_not_found", extra={"signal_id": signal_id})
        return

    dup_key = redis_keys.decision_idempotency(str(signal_row.id))
    is_duplicate = not await redis_client.set(
        dup_key, "1", nx=True, ex=redis_keys.DECISION_IDEMPOTENCY_TTL_SECONDS
    )
    killswitch_enabled = await kill_switch.is_enabled(session)
    account = await load_account_state(
        session, starting_equity_usdt=account_settings.starting_equity_usdt
    )

    signal_view = SignalView(
        id=str(signal_row.id),
        symbol=signal_row.symbol,
        action=signal_row.action,
        confidence=Decimal(signal_row.confidence),
        candle_ts=signal_row.candle_ts,
        price=Decimal(signal_row.price),
        atr_14=Decimal(signal_row.atr_14),
    )

    try:
        result = evaluate(
            signal=signal_view,
            account=account,
            config=config,
            now=datetime.now(timezone.utc),
            killswitch_enabled=killswitch_enabled,
            is_duplicate_decision=is_duplicate,
        )
    except Exception:
        # PROJECT.md Section 9.4: never allowed to propagate into an approval.
        logger.exception(
            "risk_rule_evaluation_failed",
            extra={"trace_id": str(signal_row.trace_id), "signal_id": signal_id},
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
        equity_snapshot_usdt=result.equity_snapshot_usdt,
        risk_pct_applied=result.risk_pct_applied,
    )
    session.add(decision)

    event_type = AuditEventType.RISK_APPROVED if result.approved else AuditEventType.RISK_REJECTED
    rejection_reason_value = result.rejection_reason.value if result.rejection_reason else None
    session.add(
        AuditEvent(
            trace_id=signal_row.trace_id,
            event_type=event_type.value,
            payload={
                "signal_id": str(signal_row.id),
                "approved": result.approved,
                "rejection_reason": rejection_reason_value,
            },
        )
    )
    await session.commit()

    logger.info(
        "risk_decision_recorded",
        extra={
            "trace_id": str(signal_row.trace_id),
            "signal_id": str(signal_row.id),
            "approved": result.approved,
            "rejection_reason": result.rejection_reason.value if result.rejection_reason else None,
        },
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
    config: RiskConfig,
    account_settings: AccountSettings,
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
            await process_signal(session, redis_client, signal_id, config, account_settings)
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
    config = RiskConfig()
    account_settings = AccountSettings()
    session_factory = get_session_factory()
    redis_client = redis.from_url(RedisSettings().redis_url, decode_responses=True)

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
                    session_factory, redis_client, config, account_settings, message_id, fields
                )


if __name__ == "__main__":
    asyncio.run(run_consumer())
