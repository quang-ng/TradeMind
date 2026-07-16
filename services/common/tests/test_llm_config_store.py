"""Integration tests for common/llm_config_store.py — exercises real
Postgres reads/writes. Skips gracefully if no Postgres is reachable."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from common.config import DatabaseSettings, LLMServiceSettings
from common.llm_config_store import apply_llm_config_patch, load_effective_llm_config


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
        await session.execute(text("UPDATE llm_config_state SET overrides = NULL WHERE id = 1"))
        await session.commit()

    yield session_factory
    await engine.dispose()


async def test_load_effective_config_defaults_to_env_when_no_overrides(db_session_factory):
    async with db_session_factory() as session:
        effective = await load_effective_llm_config(session)
    settings = LLMServiceSettings()
    assert effective.llm_provider == settings.llm_provider
    assert effective.anthropic_model == settings.anthropic_model
    assert effective.ollama_model == settings.ollama_model
    assert effective.ollama_temperature == settings.ollama_temperature


async def test_patch_persists_and_is_visible_on_next_load(db_session_factory):
    async with db_session_factory() as session:
        effective = await apply_llm_config_patch(session, {"ollama_temperature": 0.9})
        await session.commit()
    assert effective.ollama_temperature == 0.9

    async with db_session_factory() as session:
        reloaded = await load_effective_llm_config(session)
    assert reloaded.ollama_temperature == 0.9
    # Untouched fields keep their env default.
    assert reloaded.llm_provider == LLMServiceSettings().llm_provider


async def test_patch_merges_with_previous_overrides_instead_of_replacing(db_session_factory):
    async with db_session_factory() as session:
        await apply_llm_config_patch(session, {"llm_provider": "ollama"})
        await session.commit()

    async with db_session_factory() as session:
        await apply_llm_config_patch(session, {"ollama_temperature": 0.6})
        await session.commit()

    async with db_session_factory() as session:
        effective = await load_effective_llm_config(session)
    assert effective.llm_provider == "ollama"
    assert effective.ollama_temperature == 0.6


async def test_load_never_leaks_the_api_key_field(db_session_factory):
    """`EffectiveLLMConfig` intentionally has no `llm_api_key` field — the
    Scheduler forwards this over HTTP to llm_service (PROJECT.md Section 3),
    so the secret must never be part of what gets loaded/persisted here."""
    async with db_session_factory() as session:
        effective = await load_effective_llm_config(session)
    assert not hasattr(effective, "llm_api_key")
