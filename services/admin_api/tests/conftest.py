"""Shared fixtures for admin_api integration tests — exercises real
Postgres reads/writes (see services/risk_engine/tests/test_main_integration.py
for the same rationale). Skips gracefully if no Postgres is reachable."""

import httpx
import pytest
from admin_api.app.auth import get_admin_api_settings
from admin_api.app.deps import get_db_session
from admin_api.app.main import app
from common.config import AdminApiSettings, DatabaseSettings
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_API_KEY = "test-admin-api-key"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_API_KEY}"}

_TABLES = (
    "audit_events",
    "positions",
    "orders",
    "risk_decisions",
    "signals",
)


@pytest.fixture(autouse=True)
def _admin_api_settings():
    app.dependency_overrides[get_admin_api_settings] = lambda: AdminApiSettings(
        admin_api_key=TEST_API_KEY
    )
    yield
    app.dependency_overrides.pop(get_admin_api_settings, None)


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
        for table in _TABLES:
            await session.execute(text(f"DELETE FROM {table}"))
        await session.execute(text("UPDATE system_state SET killswitch_enabled = false"))
        await session.execute(text("UPDATE risk_config_state SET overrides = NULL WHERE id = 1"))
        await session.commit()

    async def override_get_db_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    yield session_factory
    app.dependency_overrides.pop(get_db_session, None)
    await engine.dispose()


@pytest.fixture
async def client(db_session_factory):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return dict(AUTH_HEADERS)
