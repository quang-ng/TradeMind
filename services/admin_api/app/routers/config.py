import uuid

from common.config import RiskConfig
from common.db.models import AuditEvent
from common.enums import AuditEventType
from common.llm_config_store import (
    EffectiveLLMConfig,
    apply_llm_config_patch,
    load_effective_llm_config,
)
from common.risk_config_store import apply_risk_config_patch, load_effective_risk_config
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..deps import get_db_session
from ..schemas import LLMConfigOut, LLMConfigPatch, RiskConfigOut, RiskConfigPatch

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/config", response_model=RiskConfigOut)
async def get_config(session: AsyncSession = Depends(get_db_session)) -> RiskConfig:
    """PROJECT.md Section 11 `GET /config` — current effective risk engine
    parameters (env defaults + any persisted `PATCH /config` overrides)."""
    return await load_effective_risk_config(session)


@router.patch("/config", response_model=RiskConfigOut)
async def patch_config(
    patch: RiskConfigPatch, session: AsyncSession = Depends(get_db_session)
) -> RiskConfig:
    """PROJECT.md Section 11 `PATCH /config` — updates risk parameters and
    writes a `CONFIG_CHANGED` audit event (Section 14 rule 7). Changing
    `dry_run` requires `confirm_dry_run_change=true` (Section 14 rule 13:
    flipping dry-run is a deliberate human decision, never a side effect).

    Note this only governs `RiskConfig.dry_run` (used for `Order.dry_run`
    bookkeeping and Section 9's rule evaluation) — it cannot and does not
    flip Freqtrade's actual live-trading mode, which is a separate `.env`
    `DRY_RUN` value baked into the Freqtrade container at start and is
    intentionally not runtime-editable (Section 14 rule 13)."""
    changes = patch.model_dump(exclude={"confirm_dry_run_change"}, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no fields to update")

    current = await load_effective_risk_config(session)
    if (
        "dry_run" in changes
        and changes["dry_run"] != current.dry_run
        and not patch.confirm_dry_run_change
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "changing dry_run requires confirm_dry_run_change=true "
                "(PROJECT.md Section 14 rule 13)"
            ),
        )

    effective = await apply_risk_config_patch(session, changes)
    session.add(
        AuditEvent(
            trace_id=uuid.uuid4(),
            event_type=AuditEventType.CONFIG_CHANGED.value,
            payload={"changes": jsonable_encoder(changes)},
        )
    )
    await session.commit()
    return effective


@router.get("/config/llm", response_model=LLMConfigOut)
async def get_llm_config(session: AsyncSession = Depends(get_db_session)) -> EffectiveLLMConfig:
    """PROJECT.md Section 8.4 — current effective LLM provider/model/
    temperature (env defaults + any persisted `PATCH /config/llm`
    overrides). `llm_service` itself never reads this; the Scheduler does
    and forwards it per-request (Section 3: llm_service stays off `core_net`)."""
    return await load_effective_llm_config(session)


@router.patch("/config/llm", response_model=LLMConfigOut)
async def patch_llm_config(
    patch: LLMConfigPatch, session: AsyncSession = Depends(get_db_session)
) -> EffectiveLLMConfig:
    """PROJECT.md Section 8.4 `PATCH /config/llm` — updates the LLM
    decision-engine levers and writes a `CONFIG_CHANGED` audit event. Takes
    effect on the Scheduler's next cycle, no `llm_service` restart needed."""
    changes = patch.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no fields to update")

    effective = await apply_llm_config_patch(session, changes)
    session.add(
        AuditEvent(
            trace_id=uuid.uuid4(),
            event_type=AuditEventType.CONFIG_CHANGED.value,
            payload={"changes": jsonable_encoder(changes), "scope": "llm"},
        )
    )
    await session.commit()
    return effective
