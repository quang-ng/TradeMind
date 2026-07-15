from common.config import SchedulerSettings
from fastapi import APIRouter, Depends, HTTPException, status
from scheduler.app.jobs import run_cycle

from ..auth import require_api_key
from ..schemas import CycleTriggerResponse

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/cycles/{symbol}/trigger", response_model=CycleTriggerResponse)
async def trigger_cycle(symbol: str) -> CycleTriggerResponse:
    """PROJECT.md Section 11 `POST /cycles/{symbol}/trigger` — manually
    triggers a cycle out-of-band (debugging), subject to all normal risk
    rules. This calls the exact same `run_cycle` the Scheduler uses
    (Section 5.1 steps 1-4: lock, fetch, LLM call, publish signal) — it does
    not talk to Freqtrade or approve anything itself, so it stays within the
    Administration Zone's "never place trades directly" boundary (Section
    4). The Risk Engine still evaluates the resulting signal exactly as it
    would for a scheduler-triggered cycle.

    Before Phase 5 wires a real APScheduler cron (PROJECT.md Section 12),
    this endpoint is the only way to run a cycle at all outside a test."""
    normalized = symbol.replace("-", "/").upper()
    if normalized not in SchedulerSettings().symbols:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown symbol {symbol!r}"
        )
    trace_id = await run_cycle(normalized)
    return CycleTriggerResponse(trace_id=trace_id, skipped=trace_id is None)
