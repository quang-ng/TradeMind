import uuid

from common import kill_switch
from common.db.models import SystemState
from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..deps import get_db_session, get_redis_client
from ..schemas import KillswitchRequest, KillswitchResponse

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/killswitch/enable", response_model=KillswitchResponse)
async def enable_killswitch(
    body: KillswitchRequest,
    session: AsyncSession = Depends(get_db_session),
    redis_client: Redis = Depends(get_redis_client),
) -> KillswitchResponse:
    """PROJECT.md Section 11 `POST /killswitch/enable` — halts all new
    entries immediately (PROJECT.md Section 13)."""
    return await _apply(session, redis_client, body, enable=True)


@router.post("/killswitch/disable", response_model=KillswitchResponse)
async def disable_killswitch(
    body: KillswitchRequest,
    session: AsyncSession = Depends(get_db_session),
    redis_client: Redis = Depends(get_redis_client),
) -> KillswitchResponse:
    """PROJECT.md Section 11 `POST /killswitch/disable` — resumes normal
    operation."""
    return await _apply(session, redis_client, body, enable=False)


async def _apply(
    session: AsyncSession, redis_client: Redis, body: KillswitchRequest, *, enable: bool
) -> KillswitchResponse:
    updated_by = body.updated_by or "api:admin"
    trace_id = uuid.uuid4()
    action = kill_switch.enable if enable else kill_switch.disable
    await action(
        session,
        redis_client,
        reason=body.reason,
        updated_by=updated_by,
        trace_id=trace_id,
    )
    await session.commit()

    state = await session.get(SystemState, 1)
    assert state is not None
    return KillswitchResponse(
        killswitch_enabled=state.killswitch_enabled,
        updated_by=state.killswitch_updated_by,
        updated_at=state.updated_at,
    )
