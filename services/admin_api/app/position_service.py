from decimal import Decimal

from common.db.models import Position, Signal
from common.enums import PositionStatus
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import PositionOut


def position_with_mark(position: Position, signal: Signal | None) -> PositionOut:
    """Add gross mark-to-market values from the latest closed market candle."""
    output = PositionOut.model_validate(position)
    if position.status != PositionStatus.OPEN.value or signal is None:
        return output

    current_price = Decimal(signal.price)
    current_value = position.amount * current_price
    unrealized_pnl = (current_price - position.entry_price) * position.amount
    unrealized_pnl_pct = (
        (current_price - position.entry_price) / position.entry_price
        if position.entry_price > 0
        else Decimal("0")
    )
    return output.model_copy(
        update={
            "current_price": current_price,
            "current_value_usdt": current_value,
            "unrealized_pnl_usdt": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "price_updated_at": signal.candle_ts,
        }
    )


async def list_positions_with_marks(
    session: AsyncSession, *, status_filter: str | None
) -> list[PositionOut]:
    stmt = select(Position).order_by(Position.opened_at.desc())
    if status_filter is not None:
        stmt = stmt.where(Position.status == status_filter)
    positions = list((await session.execute(stmt)).scalars().all())

    open_symbols = {
        position.symbol
        for position in positions
        if position.status == PositionStatus.OPEN.value
    }
    latest_by_symbol: dict[str, Signal] = {}
    if open_symbols:
        latest_candles = (
            select(Signal.symbol, func.max(Signal.candle_ts).label("candle_ts"))
            .where(Signal.symbol.in_(open_symbols))
            .group_by(Signal.symbol)
            .subquery()
        )
        latest_signals = (
            await session.execute(
                select(Signal).join(
                    latest_candles,
                    (Signal.symbol == latest_candles.c.symbol)
                    & (Signal.candle_ts == latest_candles.c.candle_ts),
                )
            )
        ).scalars().all()
        latest_by_symbol = {signal.symbol: signal for signal in latest_signals}

    return [
        position_with_mark(position, latest_by_symbol.get(position.symbol))
        for position in positions
    ]
