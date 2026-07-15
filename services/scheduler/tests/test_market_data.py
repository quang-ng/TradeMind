from scheduler.app.market_data import fetch_closed_candles

HOUR_MS = 3_600_000
BASE_MS = 1_700_000_000_000


class FakeExchange:
    def __init__(self, candles: list[list[float]], now_ms: float, timeframe_seconds: int = 3600):
        self._candles = candles
        self._now_ms = now_ms
        self._timeframe_seconds = timeframe_seconds
        self.closed = False

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list[list[float]]:
        return self._candles[-limit:]

    def parse_timeframe(self, timeframe: str) -> int:
        return self._timeframe_seconds

    def milliseconds(self) -> float:
        return self._now_ms

    async def close(self) -> None:
        self.closed = True


def _candle(index: int) -> list[float]:
    ts = BASE_MS + index * HOUR_MS
    return [ts, 1.0, 2.0, 0.5, 1.5, 100.0]


async def test_fetch_closed_candles_drops_the_still_forming_candle():
    raw = [_candle(i) for i in range(4)]
    now_ms = raw[-1][0] + 1_000  # last candle opened 1s ago, hasn't closed yet

    exchange = FakeExchange(raw, now_ms=now_ms)
    result = await fetch_closed_candles("BTC/USDT", limit=10, exchange=exchange)

    assert len(result) == 3
    assert result[-1]["t"] == raw[2][0]
    assert not exchange.closed


async def test_fetch_closed_candles_keeps_every_candle_once_all_are_closed():
    raw = [_candle(i) for i in range(3)]
    now_ms = raw[-1][0] + HOUR_MS + 1  # last candle's close time has elapsed

    exchange = FakeExchange(raw, now_ms=now_ms)
    result = await fetch_closed_candles("BTC/USDT", limit=10, exchange=exchange)

    assert len(result) == 3
    assert result[-1]["t"] == raw[-1][0]


async def test_fetch_closed_candles_respects_limit():
    raw = [_candle(i) for i in range(20)]
    now_ms = raw[-1][0] + HOUR_MS + 1

    exchange = FakeExchange(raw, now_ms=now_ms)
    result = await fetch_closed_candles("BTC/USDT", limit=5, exchange=exchange)

    assert len(result) == 5
    assert result[-1]["t"] == raw[-1][0]
