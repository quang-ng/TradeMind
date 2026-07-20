from datetime import datetime, timedelta, timezone
from decimal import Decimal

from common.enums import Action

from .schemas import Candle, SyntheticSignal


def build_demo() -> tuple[list[Candle], list[SyntheticSignal]]:
    """Two-trade fixture: one ROI winner, one stop-loss loser."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles: list[Candle] = []
    for index in range(17):
        open_time = start + timedelta(minutes=30 * index)
        close_time = open_time + timedelta(minutes=30)
        if index == 8:
            open_price, high, low, close = ("102", "103", "101", "102")
        elif index == 14:
            open_price, high, low, close = ("100", "101", "90", "91")
        elif index > 14:
            open_price, high, low, close = ("91", "92", "90", "91")
        else:
            open_price, high, low, close = ("100", "101", "99", "100")
        candles.append(
            Candle(
                symbol="BTC/USDT",
                open_time=open_time,
                close_time=close_time,
                open=Decimal(open_price),
                high=Decimal(high),
                low=Decimal(low),
                close=Decimal(close),
                volume=Decimal("100"),
            )
        )

    def signal(index: int, action: Action, confidence: str = "0.80") -> SyntheticSignal:
        return SyntheticSignal(
            symbol="BTC/USDT",
            candle_close_time=candles[index].close_time,
            action=action,
            confidence=Decimal(confidence),
            atr_14=Decimal("2"),
            reasoning=f"demo {action.value}",
        )

    signals = [
        signal(0, Action.BUY),
        signal(1, Action.BUY),  # rejected: position already open
        signal(2, Action.HOLD),
        signal(9, Action.SELL),  # rejected: ROI already closed the position
        signal(10, Action.BUY),  # rejected: pair cooldown
        signal(13, Action.BUY),
        signal(15, Action.HOLD),
    ]
    return candles, signals
