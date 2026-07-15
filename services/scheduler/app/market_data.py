from typing import Any

import ccxt.async_support as ccxt


async def fetch_closed_candles(
    symbol: str,
    timeframe: str = "1h",
    limit: int = 200,
    exchange: Any = None,
) -> list[dict]:
    """Fetch the last `limit` closed OHLCV candles from Binance's public
    market data (no API key required). Drops any candle whose close time has
    not yet elapsed, since the exchange's most recent entry can be the
    still-forming current candle — the system must only ever analyze closed
    candles (PROJECT.md Section 5)."""
    owns_exchange = exchange is None
    exchange = exchange or ccxt.binance()
    try:
        raw = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit + 1)
        timeframe_ms = exchange.parse_timeframe(timeframe) * 1000
        now_ms = exchange.milliseconds()
    finally:
        if owns_exchange:
            await exchange.close()

    candles = [
        {"t": ts, "o": o, "h": h, "l": low, "c": c, "v": v}
        for ts, o, h, low, c, v in raw
        if ts + timeframe_ms <= now_ms
    ]
    return candles[-limit:]
