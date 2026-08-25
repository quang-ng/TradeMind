from llm_service.app.context.builder import ContextBuilder
from llm_service.app.models.volatility import VolatilityRegime
from llm_service.app.models.wire import AnalyzeRequest
from llm_service.app.strategies.volatility_classifier import VolatilityClassifier


def _context(*, price: float, atr_14: float):
    request = AnalyzeRequest.model_validate(
        {
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "candle_close_time": "2026-07-15T13:00:00Z",
            "ohlcv": [
                {"t": "1", "o": price, "h": price, "l": price, "c": price, "v": 10.0}
            ],
            "indicators": {
                "rsi_14": 50.0,
                "ema_50": price,
                "ema_200": price,
                "macd": {"macd": 0.0, "signal": 0.0, "histogram": 0.0},
                "atr_14": atr_14,
                "volume_sma_20": 10.0,
            },
            "position_context": {"has_open_position": False, "unrealized_pnl_pct": None},
        }
    )
    return ContextBuilder().build(request)


def test_classifies_low_volatility_below_threshold():
    # atr_pct = 4 / 1000 = 0.004 < 0.005
    context = _context(price=1000.0, atr_14=4.0)

    assert VolatilityClassifier().classify(context) == VolatilityRegime.LOW_VOLATILITY


def test_classifies_normal_volatility_inside_the_band():
    # atr_pct = 10 / 1000 = 0.01, between 0.005 and 0.02
    context = _context(price=1000.0, atr_14=10.0)

    assert VolatilityClassifier().classify(context) == VolatilityRegime.NORMAL


def test_classifies_high_volatility_above_threshold():
    # atr_pct = 30 / 1000 = 0.03 > 0.02
    context = _context(price=1000.0, atr_14=30.0)

    assert VolatilityClassifier().classify(context) == VolatilityRegime.HIGH_VOLATILITY


def test_boundary_values_are_inclusive_of_normal():
    # Exactly at both thresholds -> NORMAL (strict < / > in the classifier).
    assert VolatilityClassifier().classify(_context(price=1000.0, atr_14=5.0)) == (
        VolatilityRegime.NORMAL
    )
    assert VolatilityClassifier().classify(_context(price=1000.0, atr_14=20.0)) == (
        VolatilityRegime.NORMAL
    )
