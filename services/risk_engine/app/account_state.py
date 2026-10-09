from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from common.account_balance import AccountBalanceSnapshot
from common.db.models import Position, SystemState
from common.enums import PositionStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import AccountState


def _count_consecutive_losses(
    closed_positions: Sequence[Position],
    *,
    reset_at: datetime | None,
    cluster_window: timedelta,
) -> tuple[int, datetime | None]:
    """Count the newest uninterrupted loss streak after operator reset, in
    loss *events* rather than losing positions (issue #26).

    `closed_positions` must be ordered newest close first. The streak is the
    run of losing positions before the first non-loss or the reset boundary.
    Walking that streak oldest-first, a loss closed within `cluster_window`
    of the first loss of the current cluster joins it; otherwise it opens a
    new cluster. Each cluster is one event, so a single market dip that stops
    several correlated pairs within minutes counts once, while losses spread
    across separate dips still accumulate. Anchoring on the cluster's first
    loss (not the previous loss) keeps a slow bleed of losses from chaining
    into one endless cluster. A loss without `closed_at` is its own event.
    """
    streak_close_times: list[datetime | None] = []
    for position in closed_positions:
        if (
            reset_at is not None
            and position.closed_at is not None
            and position.closed_at <= reset_at
        ):
            break
        if position.pnl_usdt is not None and position.pnl_usdt < 0:
            streak_close_times.append(position.closed_at)
        else:
            break

    loss_events = 0
    cluster_start: datetime | None = None
    for closed_at in reversed(streak_close_times):
        if (
            closed_at is None
            or cluster_start is None
            or closed_at - cluster_start > cluster_window
        ):
            loss_events += 1
            cluster_start = closed_at

    last_loss_closed_at = streak_close_times[0] if streak_close_times else None
    return loss_events, last_loss_closed_at


async def load_account_state(
    session: AsyncSession,
    *,
    balance: AccountBalanceSnapshot,
    loss_cluster_window: timedelta,
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
    system_state = await session.get(SystemState, 1)
    consecutive_loss_reset_at = (
        system_state.consecutive_loss_reset_at if system_state is not None else None
    )

    symbol_last_closed_at: dict[str, datetime] = {}
    for position in closed_positions:
        if position.closed_at is not None:
            symbol_last_closed_at.setdefault(position.symbol, position.closed_at)

    consecutive_losses, last_loss_closed_at = _count_consecutive_losses(
        closed_positions,
        reset_at=consecutive_loss_reset_at,
        cluster_window=loss_cluster_window,
    )

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
