from datetime import datetime, timezone
from decimal import Decimal

from common import redis_keys
from common.account_balance import AccountBalanceSnapshot
from common.config import SchedulerSettings
from common.db.models import Position, Signal, SystemState
from common.enums import PositionStatus
from common.risk_config_store import load_effective_risk_config
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..deps import get_db_session, get_redis_client
from ..schemas import PairStatus, StatusOut

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/status", response_model=StatusOut)
async def get_status(
    session: AsyncSession = Depends(get_db_session),
    redis_client: Redis = Depends(get_redis_client),
) -> StatusOut:
    """PROJECT.md Section 11 `GET /status`.

    Account equity is never synthesized. The endpoint fails unavailable when
    the Risk Engine has not published a fresh, typed Freqtrade snapshot.
    """
    state = await session.get(SystemState, 1)
    killswitch_enabled = bool(state and state.killswitch_enabled)
    config = await load_effective_risk_config(session)

    open_positions = (
        (
            await session.execute(
                select(Position).where(Position.status == PositionStatus.OPEN.value)
            )
        )
        .scalars()
        .all()
    )

    raw_balance = await redis_client.get(redis_keys.ACCOUNT_BALANCE_SNAPSHOT_KEY)
    try:
        balance = (
            AccountBalanceSnapshot.model_validate_json(raw_balance)
            if raw_balance is not None
            else None
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="account balance unavailable",
        ) from exc
    if balance is None or not balance.is_fresh(
        now=datetime.now(timezone.utc),
        max_age_seconds=redis_keys.ACCOUNT_BALANCE_SNAPSHOT_TTL_SECONDS,
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="account balance unavailable",
        )

    today = datetime.now(timezone.utc).date()
    closed_positions = (
        (
            await session.execute(
                select(Position).where(Position.status == PositionStatus.CLOSED.value)
            )
        )
        .scalars()
        .all()
    )
    equity_usdt = balance.equity_usdt
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
        free_balance_usdt=balance.free_balance_usdt,
        daily_pnl_pct=daily_pnl_pct,
        pairs=pairs,
    )
