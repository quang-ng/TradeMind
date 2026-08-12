"""DB fixture for notifier tests — same rationale/pattern as
services/admin_api/tests/conftest.py: exercises real Postgres, skips
gracefully if none is reachable."""

import pytest
from common.config import DatabaseSettings
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_TABLES = ("positions", "orders", "risk_decisions", "signals")


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
        await session.commit()

    yield session_factory
    await engine.dispose()
