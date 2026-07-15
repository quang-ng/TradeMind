import uuid

from common.db.models import AuditEvent, Order, RiskDecision, Signal
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..deps import get_db_session
from ..schemas import AuditTimelineOut

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/audit", response_model=AuditTimelineOut)
async def get_audit_timeline(
    trace_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> AuditTimelineOut:
    """PROJECT.md Section 11 `GET /audit?trace_id=` — "Full timeline for one
    trading cycle". Every row created during a single cycle run shares this
    `trace_id` (Section 7's opening paragraph), so one query per table
    reconstructs the whole decision path."""
    signals = (
        (
            await session.execute(
                select(Signal).where(Signal.trace_id == trace_id).order_by(Signal.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    risk_decisions = (
        (
            await session.execute(
                select(RiskDecision)
                .where(RiskDecision.trace_id == trace_id)
                .order_by(RiskDecision.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    orders = (
        (
            await session.execute(
                select(Order).where(Order.trace_id == trace_id).order_by(Order.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    audit_events = (
        (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.trace_id == trace_id)
                .order_by(AuditEvent.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    return AuditTimelineOut(
        trace_id=trace_id,
        signals=list(signals),
        risk_decisions=list(risk_decisions),
        orders=list(orders),
        audit_events=list(audit_events),
    )
