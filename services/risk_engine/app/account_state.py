from datetime import datetime, timezone
from decimal import Decimal

from common.account_balance import AccountBalanceSnapshot
from common.db.models import Position
from common.enums import PositionStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import AccountState


async def load_account_state(
    session: AsyncSession, *, balance: AccountBalanceSnapshot
) -> AccountState:
    """Builds the Section 9.1/9.2 account-state inputs from Postgres.

    Equity and free stake balance come exclusively from Freqtrade's
    authenticated `/balance` endpoint. Callers must obtain a fresh typed
    snapshot for this decision; no configured or cached fallback is accepted.
    """
    open_positions = (
        (
            await session.execute(
                select(Position).where(Position.status == PositionStatus.OPEN.value)
            )
        )
        .scalars()
        .all()
    )
    open_position_symbols = frozenset(p.symbol for p in open_positions)
    total_exposure_usdt = sum(
        (p.amount * p.entry_price for p in open_positions), start=Decimal("0")
    )

    closed_positions = (
        (
            await session.execute(
                select(Position)
                .where(
                    Position.status == PositionStatus.CLOSED.value,
                    Position.closed_at.is_not(None),
                )
                .order_by(Position.closed_at.desc())
            )
        )
        .scalars()
        .all()
    )

    symbol_last_closed_at: dict[str, datetime] = {}
    for position in closed_positions:
        if position.closed_at is not None:
            symbol_last_closed_at.setdefault(position.symbol, position.closed_at)

    consecutive_losses = 0
    last_loss_closed_at: datetime | None = None
    for position in closed_positions:
        if position.pnl_usdt is not None and position.pnl_usdt < 0:
            consecutive_losses += 1
            if last_loss_closed_at is None:
                last_loss_closed_at = position.closed_at
        else:
            break

    equity_usdt = balance.equity_usdt
    today = datetime.now(timezone.utc).date()
    daily_pnl_usdt = sum(
        (
            position.pnl_usdt or Decimal("0")
            for position in closed_positions
            if position.closed_at is not None and position.closed_at.date() == today
        ),
        start=Decimal("0"),
    )
    daily_pnl_pct = (daily_pnl_usdt / equity_usdt) if equity_usdt > 0 else Decimal("0")

    return AccountState(
        equity_usdt=equity_usdt,
        free_balance_usdt=balance.free_balance_usdt,
        open_position_symbols=open_position_symbols,
        total_exposure_usdt=total_exposure_usdt,
        daily_pnl_pct=daily_pnl_pct,
        consecutive_losses=consecutive_losses,
        last_loss_closed_at=last_loss_closed_at,
        symbol_last_closed_at=symbol_last_closed_at,
    )
