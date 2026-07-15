"""Integration tests for common/risk_config_store.py — exercises real
Postgres reads/writes. Skips gracefully if no Postgres is reachable."""

from decimal import Decimal

import pytest
from common.config import DatabaseSettings, RiskConfig
from common.risk_config_store import apply_risk_config_patch, load_effective_risk_config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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
        await session.execute(text("UPDATE risk_config_state SET overrides = NULL WHERE id = 1"))
        await session.commit()

    yield session_factory
    await engine.dispose()


async def test_load_effective_config_defaults_to_env_when_no_overrides(db_session_factory):
    async with db_session_factory() as session:
        effective = await load_effective_risk_config(session)
    assert effective == RiskConfig()


async def test_patch_persists_and_is_visible_on_next_load(db_session_factory):
    async with db_session_factory() as session:
        effective = await apply_risk_config_patch(session, {"min_confidence": Decimal("0.80")})
        await session.commit()
    assert effective.min_confidence == Decimal("0.80")

    async with db_session_factory() as session:
        reloaded = await load_effective_risk_config(session)
    assert reloaded.min_confidence == Decimal("0.80")
    # Untouched fields keep their env default.
    assert reloaded.max_open_positions == RiskConfig().max_open_positions


async def test_patch_merges_with_previous_overrides_instead_of_replacing(db_session_factory):
    async with db_session_factory() as session:
        await apply_risk_config_patch(session, {"min_confidence": Decimal("0.80")})
        await session.commit()

    async with db_session_factory() as session:
        await apply_risk_config_patch(session, {"cooldown_minutes": 30})
        await session.commit()

    async with db_session_factory() as session:
        effective = await load_effective_risk_config(session)
    assert effective.min_confidence == Decimal("0.80")
    assert effective.cooldown_minutes == 30
