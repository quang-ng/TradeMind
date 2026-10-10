"""Candle loading + vectorized indicators for the strategy lab (`lab.py`).

Indicators reuse `scheduler/app/indicators.py`'s own functions, so EMA/RSI/
MACD/ATR are the same math the live Scheduler sends to `/analyze`. One
known difference: live computes them over a trailing `candle_lookback`
(200) window per decision, the lab over the full history. EMA200 seeded
200 candles back vs. years back differs slightly, so the lab's
`current_system` is a close approximation of live, not a byte-exact replay
(that is `mechanical_replay.py`'s job, at ~100x the cost).
"""

from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401,I001 -- must patch sys.path before the imports below
import pandas as pd
from cache import CandleCache  # noqa: E402
from scheduler.app.indicators import atr, ema, macd, rsi  # noqa: E402

_HOUR_MS = 3_600_000
# Timeframes the lab can build from the cached 1h series. Coarser frames are
# resampled from 1h (never fetched separately) so every timeframe sees the
# exact same underlying trades.
TIMEFRAME_HOURS = {"1h": 1, "4h": 4}


def to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def load_candles(
    cache: CandleCache, symbol: str, timeframe: str, start: datetime, end: datetime
) -> pd.DataFrame:
    """OHLCV indexed by candle *open* time (UTC), `[start, end)`. Coarser
    timeframes drop any bucket missing one of its 1h candles rather than
    building a partial candle."""
    rows = cache.get_range(symbol, "1h", to_ms(start), to_ms(end))
    if not rows:
        return pd.DataFrame(columns=["o", "h", "l", "c", "v"])
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop("t"), unit="ms", utc=True)
    hours = TIMEFRAME_HOURS[timeframe]
    if hours == 1:
        return df[["o", "h", "l", "c", "v"]]
    grouped = df.resample(f"{hours}h", label="left", closed="left")
    out = grouped.agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"})
    return out[grouped["c"].count() == hours]


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Every column a lab strategy reads, computed once per series. Row `i`
    only ever uses candles `<= i` (or `< i` where noted), so a decision at
    the close of `i` never sees the future."""
    out = df.copy()
    close = out["c"]
    out["ema50"] = ema(close, 50)
    out["ema200"] = ema(close, 200)
    out["rsi14"] = rsi(close, 14)
    m = macd(close)
    out["macd"], out["macd_signal"], out["macd_hist"] = m["macd"], m["signal"], m["histogram"]
    out["atr14"] = atr(out["h"], out["l"], close, 14)
    out["vol_sma20"] = out["v"].rolling(20).mean()
    out["ema_gap"] = (out["ema50"] - out["ema200"]) / out["ema200"]

    mid = close.rolling(20).mean()
    std = close.rolling(20).std(ddof=0)
    out["bb_mid"], out["bb_upper"], out["bb_lower"] = mid, mid + 2 * std, mid - 2 * std
    out["bb_width"] = (out["bb_upper"] - out["bb_lower"]) / mid
    # Prior-bar extremes (exclude the current candle): a breakout above the
    # last 20 highs, not above a window that already contains itself.
    out["hh20_prev"] = out["h"].shift(1).rolling(20).max()
    out["ll20_prev"] = out["l"].shift(1).rolling(20).min()

    # Daily trend from *completed* UTC days only: yesterday's close vs
    # yesterday's daily EMA50, forward-filled onto every intraday candle.
    daily_close = close.resample("1D", label="left", closed="left").last().dropna()
    daily = pd.DataFrame({"close": daily_close, "ema50": ema(daily_close, 50)}).shift(1)
    day_key = out.index.floor("1D")
    out["daily_close_prev"] = daily["close"].reindex(day_key).to_numpy()
    out["daily_ema50_prev"] = daily["ema50"].reindex(day_key).to_numpy()
    return out


def warmup_start(start: datetime, timeframe: str, bars: int = 400) -> datetime:
    """Earlier fetch start so EMA200 and the daily EMA50 are seeded before
    `start`."""
    hours = max(TIMEFRAME_HOURS[timeframe] * bars, 24 * 60)
    return datetime.fromtimestamp((to_ms(start) - hours * _HOUR_MS) / 1000, tz=timezone.utc)


def open_cache(cache_dir: Path) -> CandleCache:
    return CandleCache(cache_dir / "candles.sqlite")
