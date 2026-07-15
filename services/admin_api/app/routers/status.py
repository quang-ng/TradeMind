from datetime import datetime, timezone
from decimal import Decimal

from common.config import AccountSettings, SchedulerSettings
from common.db.models import Position, Signal, SystemState
from common.enums import PositionStatus
from common.risk_config_store import load_effective_risk_config
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..deps import get_db_session
from ..schemas import PairStatus, StatusOut

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/status", response_model=StatusOut)
async def get_status(session: AsyncSession = Depends(get_db_session)) -> StatusOut:
    """PROJECT.md Section 11 `GET /status`."""
    state = await session.get(SystemState, 1)
    killswitch_enabled = bool(state and state.killswitch_enabled)
    config = await load_effective_risk_config(session)

    open_positions = (
        (await session.execute(select(Position).where(Position.status == PositionStatus.OPEN.value)))
        .scalars()
        .all()
    )

    equity_usdt = AccountSettings().starting_equity_usdt
    today = datetime.now(timezone.utc).date()
    closed_positions = (
        (await session.execute(select(Position).where(Position.status == PositionStatus.CLOSED.value)))
        .scalars()
        .all()
    )
    daily_pnl_usdt = sum(
        (
            position.pnl_usdt or Decimal("0")
            for position in closed_positions
            if position.closed_at is not None and position.closed_at.date() == today
        ),
        start=Decimal("0"),
    )
    daily_pnl_pct = (daily_pnl_usdt / equity_usdt) if equity_usdt > 0 else Decimal("0")

    pairs: dict[str, PairStatus] = {}
    for symbol in SchedulerSettings().symbols:
        latest_signal = (
            await session.execute(
                select(Signal)
                .where(Signal.symbol == symbol)
                .order_by(Signal.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        pairs[symbol] = PairStatus(
            last_cycle_at=latest_signal.created_at if latest_signal else None,
            last_action=latest_signal.action if latest_signal else None,
        )

    return StatusOut(
        killswitch_enabled=killswitch_enabled,
        dry_run=config.dry_run,
        open_positions=len(open_positions),
        equity_usdt=equity_usdt,
        daily_pnl_pct=daily_pnl_pct,
        pairs=pairs,
    )
