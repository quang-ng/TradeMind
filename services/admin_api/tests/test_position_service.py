import uuid
from datetime import datetime, timezone
from decimal import Decimal

from admin_api.app.position_service import position_with_mark
from common.db.models import Position, Signal
from common.enums import Action, PositionStatus, SignalStatus

NOW = datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc)


def _position(*, status: str = PositionStatus.OPEN.value) -> Position:
    return Position(
        id=uuid.uuid4(),
        symbol="BTC/USDT",
        status=status,
        entry_order_id=uuid.uuid4(),
        entry_price=Decimal("60000"),
        amount=Decimal("0.01"),
        opened_at=NOW,
    )


def _signal() -> Signal:
    return Signal(
        id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
        symbol="BTC/USDT",
        timeframe="30m",
        candle_ts=NOW,
        action=Action.HOLD.value,
        confidence=Decimal("0.60"),
        reasoning="mark",
        model_name="test:model",
        price=Decimal("61000"),
        atr_14=Decimal("500"),
        status=SignalStatus.CONSUMED.value,
    )


def test_open_position_includes_latest_mark_to_market_values() -> None:
    result = position_with_mark(_position(), _signal())

    assert result.current_price == Decimal("61000")
    assert result.current_value_usdt == Decimal("610")
    assert result.unrealized_pnl_usdt == Decimal("10")
    assert result.unrealized_pnl_pct == Decimal("0.01666666666666666666666666667")
    assert result.price_updated_at == NOW


def test_position_without_market_mark_keeps_derived_values_empty() -> None:
    result = position_with_mark(_position(), None)

    assert result.current_price is None
    assert result.unrealized_pnl_usdt is None
