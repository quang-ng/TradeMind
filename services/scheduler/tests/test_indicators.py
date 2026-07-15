import pandas as pd
import pytest
from scheduler.app.indicators import atr, compute_indicators, ema, macd, rsi


def _flat_series(value: float, n: int) -> pd.Series:
    return pd.Series([value] * n)


def test_ema_of_constant_series_converges_to_the_constant():
    series = _flat_series(100.0, 60)

    result = ema(series, 20)

    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_is_high_for_a_monotonically_increasing_series():
    series = pd.Series(range(1, 60))

    result = rsi(series, 14)

    assert result.iloc[-1] > 70


def test_rsi_is_low_for_a_monotonically_decreasing_series():
    series = pd.Series(range(60, 1, -1))

    result = rsi(series, 14)

    assert result.iloc[-1] < 30


def test_atr_reflects_the_constant_high_low_spread_on_flat_candles():
    high = _flat_series(101.0, 30)
    low = _flat_series(99.0, 30)
    close = _flat_series(100.0, 30)

    result = atr(high, low, close, 14)

    assert result.iloc[-1] == pytest.approx(2.0)


def test_macd_of_a_constant_series_is_zero():
    series = _flat_series(50.0, 60)

    result = macd(series)

    assert result["macd"].iloc[-1] == pytest.approx(0.0, abs=1e-9)
    assert result["histogram"].iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_compute_indicators_returns_the_analyze_contract_shape():
    n = 210
    candles = pd.DataFrame(
        {
            "t": list(range(n)),
            "o": [100.0] * n,
            "h": [101.0] * n,
            "l": [99.0] * n,
            "c": [100.0 + i * 0.01 for i in range(n)],
            "v": [10.0] * n,
        }
    )

    result = compute_indicators(candles)

    assert set(result.keys()) == {"rsi_14", "ema_50", "ema_200", "macd", "atr_14", "volume_sma_20"}
    assert set(result["macd"].keys()) == {"macd", "signal", "histogram"}
    assert isinstance(result["rsi_14"], float)
