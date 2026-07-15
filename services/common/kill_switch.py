import uuid

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from common import redis_keys
from common.db.models import AuditEvent, SystemState
from common.enums import AuditEventType


async def is_enabled(session: AsyncSession) -> bool:
    """Postgres is the durable source of truth for the kill switch
    (PROJECT.md Section 7.6); the Risk Engine reads it directly here rather
    than the Redis cache to keep rule evaluation correct even if the cache
    and Postgres ever drift."""
    state = await session.get(SystemState, 1)
    return bool(state and state.killswitch_enabled)


async def enable(
    session: AsyncSession,
    redis_client: Redis,
    *,
    reason: str,
    updated_by: str,
    trace_id: uuid.UUID,
) -> None:
    """PROJECT.md Section 10.2: Postgres write first, Redis mirror second —
    if the Postgres write fails, the Redis write is never attempted."""
    state = await session.get(SystemState, 1)
    if state is None:
        state = SystemState(id=1)
        session.add(state)
    state.killswitch_enabled = True
    state.killswitch_reason = reason
    state.killswitch_updated_by = updated_by
    await session.flush()

    session.add(
        AuditEvent(
            trace_id=trace_id,
            event_type=AuditEventType.KILLSWITCH_ENABLED.value,
            payload={"reason": reason, "updated_by": updated_by},
        )
    )
    await session.flush()

    await redis_client.set(redis_keys.KILLSWITCH_GLOBAL_KEY, "1")


async def disable(
    session: AsyncSession,
    redis_client: Redis,
    *,
    reason: str,
    updated_by: str,
    trace_id: uuid.UUID,
) -> None:
    """Mirror of `enable` (PROJECT.md Section 11 `POST /killswitch/disable`)
    — same Postgres-first-then-Redis ordering."""
    state = await session.get(SystemState, 1)
    if state is None:
        state = SystemState(id=1)
        session.add(state)
    state.killswitch_enabled = False
    state.killswitch_reason = reason
    state.killswitch_updated_by = updated_by
    await session.flush()

    session.add(
        AuditEvent(
            trace_id=trace_id,
            event_type=AuditEventType.KILLSWITCH_DISABLED.value,
            payload={"reason": reason, "updated_by": updated_by},
        )
    )
    await session.flush()

    await redis_client.set(redis_keys.KILLSWITCH_GLOBAL_KEY, "0")
