from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from common.config import RiskConfig
from common.db.models import RiskConfigState


def _jsonable(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value


async def load_effective_risk_config(session: AsyncSession) -> RiskConfig:
    """PROJECT.md Section 9.1's `RiskConfig` is env-sourced by default;
    this layers whatever has been persisted via `PATCH /config`
    (Section 11) on top, so a config change takes effect on the next signal
    without a service restart. `RiskConfig(**merged)` re-validates the
    merged shape, so a corrupt override can never silently produce an
    unconstructible config."""
    merged = RiskConfig().model_dump()
    state = await session.get(RiskConfigState, 1)
    if state is not None and state.overrides:
        merged.update(state.overrides)
    return RiskConfig(**merged)


async def apply_risk_config_patch(session: AsyncSession, patch: dict[str, Any]) -> RiskConfig:
    """Merges `patch` into the persisted overrides and returns the new
    effective config. Overrides are stored as a partial dict (only fields
    ever explicitly PATCHed), not a full snapshot, so future new `RiskConfig`
    fields keep picking up their env default until explicitly overridden."""
    state = await session.get(RiskConfigState, 1)
    if state is None:
        state = RiskConfigState(id=1, overrides={})
        session.add(state)

    merged_overrides = {**(state.overrides or {}), **patch}
    effective = RiskConfig(**{**RiskConfig().model_dump(), **merged_overrides})
    state.overrides = {key: _jsonable(value) for key, value in merged_overrides.items()}
    await session.flush()
    return effective
