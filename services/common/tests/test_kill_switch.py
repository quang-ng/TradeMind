"""Integration tests for common/kill_switch.py — exercises real Postgres
reads/writes (see services/risk_engine/tests/test_main_integration.py for
the same rationale). Skips gracefully if no Postgres is reachable."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from common import kill_switch
from common.config import DatabaseSettings
from common.db.models import AuditEvent, SystemState
from common.enums import AuditEventType


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True


@pytest.fixture
async def db_session_factory():
    engine = create_async_engine(DatabaseSettings().postgres_dsn)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await session.execute(select(1))
    except Exception:
        await engine.dispose()
        pytest.skip("no live Postgres reachable (set POSTGRES_DSN or run `make up`)")

    async with session_factory() as session:
        await session.execute(text("DELETE FROM audit_events"))
        await session.execute(text("UPDATE system_state SET killswitch_enabled = false"))
        await session.commit()

    yield session_factory
    await engine.dispose()


async def test_is_enabled_defaults_false(db_session_factory):
    async with db_session_factory() as session:
        assert await kill_switch.is_enabled(session) is False


async def test_enable_writes_postgres_redis_and_audit_event(db_session_factory):
    redis_client = FakeRedis()
    trace_id = uuid.uuid4()
    async with db_session_factory() as session:
        await kill_switch.enable(
            session, redis_client, reason="manual review", updated_by="api:admin", trace_id=trace_id
        )
        await session.commit()

    assert redis_client.store["killswitch:global"] == "1"
    async with db_session_factory() as session:
        assert await kill_switch.is_enabled(session) is True
        state = await session.get(SystemState, 1)
        assert state.killswitch_reason == "manual review"
        assert state.killswitch_updated_by == "api:admin"

        event = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type == AuditEventType.KILLSWITCH_ENABLED.value
                )
            )
        ).scalar_one()
        assert event.payload == {"reason": "manual review", "updated_by": "api:admin"}


async def test_disable_reverts_postgres_and_redis(db_session_factory):
    redis_client = FakeRedis()
    async with db_session_factory() as session:
        await kill_switch.enable(
            session, redis_client, reason="r1", updated_by="api:admin", trace_id=uuid.uuid4()
        )
        await session.commit()

    async with db_session_factory() as session:
        await kill_switch.disable(
            session,
            redis_client,
            reason="resume trading",
            updated_by="telegram:12345",
            trace_id=uuid.uuid4(),
        )
        await session.commit()

    assert redis_client.store["killswitch:global"] == "0"
    async with db_session_factory() as session:
        assert await kill_switch.is_enabled(session) is False
        state = await session.get(SystemState, 1)
        assert state.killswitch_updated_by == "telegram:12345"

        event = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type == AuditEventType.KILLSWITCH_DISABLED.value
                )
            )
        ).scalar_one()
        assert event.payload["updated_by"] == "telegram:12345"
