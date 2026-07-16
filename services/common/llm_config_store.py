from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from common.config import LLMServiceSettings
from common.db.models import LLMConfigState

OVERRIDABLE_FIELDS = ("llm_provider", "anthropic_model", "ollama_model", "ollama_temperature")


class EffectiveLLMConfig(BaseModel):
    """The decision-engine levers a caller may override, deliberately
    excluding `llm_service`'s secret/infra-only settings (`llm_api_key`,
    `ollama_base_url`, `analyze_timeout_seconds`) — those stay env-only on
    `llm_service` itself and are never persisted here or forwarded by the
    Scheduler (PROJECT.md Section 3: llm_service has no DB access)."""

    llm_provider: str
    anthropic_model: str
    ollama_model: str
    ollama_temperature: float


def _env_defaults() -> dict[str, Any]:
    settings = LLMServiceSettings()
    return {field: getattr(settings, field) for field in OVERRIDABLE_FIELDS}


async def load_effective_llm_config(session: AsyncSession) -> EffectiveLLMConfig:
    """Env-sourced defaults layered with whatever has been persisted via
    `PATCH /config/llm`, mirroring `risk_config_store.load_effective_risk_config`."""
    merged = _env_defaults()
    state = await session.get(LLMConfigState, 1)
    if state is not None and state.overrides:
        merged.update(state.overrides)
    return EffectiveLLMConfig(**merged)


async def apply_llm_config_patch(
    session: AsyncSession, patch: dict[str, Any]
) -> EffectiveLLMConfig:
    """Merges `patch` into the persisted overrides and returns the new
    effective config. Overrides are stored as a partial dict, not a full
    snapshot, so future new fields keep picking up their env default until
    explicitly overridden."""
    state = await session.get(LLMConfigState, 1)
    if state is None:
        state = LLMConfigState(id=1, overrides={})
        session.add(state)

    merged_overrides = {**(state.overrides or {}), **patch}
    effective = EffectiveLLMConfig(**{**_env_defaults(), **merged_overrides})
    state.overrides = merged_overrides
    await session.flush()
    return effective
