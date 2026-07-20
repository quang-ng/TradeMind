import asyncio
import logging

import aiohttp
import ccxt.async_support as ccxt
from cache import CandleCache

logger = logging.getLogger(__name__)

_TIMEFRAME_UNIT_MS = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
_MAX_ATTEMPTS = 5


def timeframe_to_ms(timeframe: str) -> int:
    return int(timeframe[:-1]) * _TIMEFRAME_UNIT_MS[timeframe[-1]]


async def _fetch_ohlcv_with_retry(exchange, symbol, timeframe, cursor):
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=1000)
        except ccxt.NetworkError as exc:
            if attempt == _MAX_ATTEMPTS:
                raise
            backoff = 2**attempt
            logger.warning(
                "binance_fetch_retry symbol=%s attempt=%d/%d error=%s backoff=%ds",
                symbol, attempt, _MAX_ATTEMPTS, exc, backoff,
            )
            await asyncio.sleep(backoff)


async def fetch_history(
    symbol: str, timeframe: str, since_ms: int, until_ms: int, cache: CandleCache
) -> list[dict]:
    """Returns closed candles in `[since_ms, until_ms)`, backed by `cache` so
    re-running a backtest over the same range never re-fetches from
    Binance. Retries transient network errors — a multi-month pull is many
    paginated requests, and one blip shouldn't kill the whole run."""
    timeframe_ms = timeframe_to_ms(timeframe)
    expected = (until_ms - since_ms) // timeframe_ms
    cached = cache.get_range(symbol, timeframe, since_ms, until_ms)
    if len(cached) >= expected:
        return cached

    # By default load_markets() also concurrently queries the linear/inverse
    # futures hosts (fapi/dapi.binance.com) to build ccxt's unified markets
    # dict — irrelevant for spot OHLCV and it multiplies DNS/connect failure
    # surface for no benefit. Restrict to spot only.
    #
    # aiohttp's default resolver (aiodns, if installed) sends raw DNS
    # queries directly to configured nameservers; some sandboxed/restricted
    # networks allow normal outbound connections (which go through the OS
    # resolver, e.g. what `curl` uses) but silently drop those raw queries.
    # ThreadedResolver routes through socket.getaddrinfo instead, so it
    # works the same way `curl` does everywhere aiodns would also work.
    connector = aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
    session = aiohttp.ClientSession(connector=connector)
    exchange = ccxt.binance(
        {
            "timeout": 30_000,
            "options": {"fetchMarkets": {"types": ["spot"]}},
            "session": session,
        }
    )
    fetched: list[dict] = []
    try:
        cursor = since_ms
        while cursor < until_ms:
            raw = await _fetch_ohlcv_with_retry(exchange, symbol, timeframe, cursor)
            if not raw:
                break
            fetched.extend(
                {"t": ts, "o": o, "h": h, "l": low, "c": c, "v": v}
                for ts, o, h, low, c, v in raw
                if ts < until_ms
            )
            next_cursor = raw[-1][0] + timeframe_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            logger.info("fetched_candles", extra={"symbol": symbol, "up_to_ts": cursor})
            await asyncio.sleep(exchange.rateLimit / 1000)
    finally:
        await exchange.close()
        await session.close()

    if fetched:
        cache.put_many(symbol, timeframe, fetched)
    return cache.get_range(symbol, timeframe, since_ms, until_ms)
