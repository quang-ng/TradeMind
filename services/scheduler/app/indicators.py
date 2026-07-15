import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + rs))
    return result.fillna(100)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal_period: int = 9) -> pd.DataFrame:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "histogram": histogram})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def compute_indicators(candles: pd.DataFrame) -> dict:
    """Compute the latest indicator snapshot for a candle series.

    `candles` must have columns t, o, h, l, c, v, ordered oldest -> newest.
    Returns a dict matching the `indicators` shape of the LLM Analysis
    Service's input contract (PROJECT.md Section 8.1)."""
    close = candles["c"]
    macd_df = macd(close)
    return {
        "rsi_14": float(rsi(close, 14).iloc[-1]),
        "ema_50": float(ema(close, 50).iloc[-1]),
        "ema_200": float(ema(close, 200).iloc[-1]),
        "macd": {
            "macd": float(macd_df["macd"].iloc[-1]),
            "signal": float(macd_df["signal"].iloc[-1]),
            "histogram": float(macd_df["histogram"].iloc[-1]),
        },
        "atr_14": float(atr(candles["h"], candles["l"], close, 14).iloc[-1]),
        "volume_sma_20": float(candles["v"].rolling(window=20).mean().iloc[-1]),
    }
