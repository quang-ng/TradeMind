from llm_service.app.context.builder import ContextBuilder
from llm_service.app.models.wire import AnalyzeRequest


def _request(**overrides) -> AnalyzeRequest:
    payload = {
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candle_close_time": "2026-07-15T13:00:00Z",
        "ohlcv": [
            {"t": "1", "o": 100, "h": 102, "l": 99, "c": 101, "v": 80},
            {"t": "2", "o": 101, "h": 103, "l": 100, "c": 102, "v": 90},
            {"t": "3", "o": 102, "h": 104, "l": 101, "c": 103, "v": 95},
        ],
        "indicators": {
            "rsi_14": 56.7,
            "ema_50": 102.0,
            "ema_200": 100.0,
            "macd": {"macd": 1.0, "signal": 0.5, "histogram": 0.5},
            "atr_14": 2.0,
            "volume_sma_20": 90.0,
        },
        "position_context": {"has_open_position": False, "unrealized_pnl_pct": None},
        **overrides,
    }
    return AnalyzeRequest.model_validate(payload)


def test_context_retains_the_original_request_for_prompt_fidelity():
    request = _request()
    context = ContextBuilder().build(request)

    assert context.request is request
    assert context.symbol == "BTC/USDT"
    assert context.position.has_open_position is False


def test_trend_metrics_reflect_uptrend_alignment():
    request = _request(
        indicators={
            "rsi_14": 56.7,
            "ema_50": 102.0,
            "ema_200": 100.0,
            "macd": {"macd": 1.0, "signal": 0.5, "histogram": 0.5},
            "atr_14": 2.0,
            "volume_sma_20": 90.0,
        }
    )
    context = ContextBuilder().build(request)

    assert context.trend.ema50_above_ema200 is True
    assert context.trend.price_above_ema50 is True
    assert context.trend.price_above_ema200 is True
    assert context.trend.ema_gap_pct == (102.0 - 100.0) / 100.0


def test_momentum_metrics_classify_bullish_macd_and_rsi_zone():
    request = _request()
    context = ContextBuilder().build(request)

    assert context.momentum.macd_bullish is True
    assert context.momentum.macd_bearish is False
    assert context.momentum.histogram_atr_ratio == 0.5 / 2.0
    assert context.momentum.rsi_zone == "bullish_neutral"


def test_rsi_zone_boundaries():
    cases = [
        (10.0, "oversold"),
        (40.0, "bearish_neutral"),
        (60.0, "bullish_neutral"),
        (90.0, "overbought"),
    ]
    for rsi, expected in cases:
        request = _request(
            indicators={
                "rsi_14": rsi,
                "ema_50": 102.0,
                "ema_200": 100.0,
                "macd": {"macd": 1.0, "signal": 0.5, "histogram": 0.5},
                "atr_14": 2.0,
                "volume_sma_20": 90.0,
            }
        )
        context = ContextBuilder().build(request)
        assert context.momentum.rsi_zone == expected


def test_volatility_and_volume_metrics():
    request = _request()
    context = ContextBuilder().build(request)

    assert context.volatility.atr_pct == 2.0 / 103.0
    assert context.volume.latest_above_sma20 is True


def test_derived_metrics_guard_against_zero_denominators_instead_of_raising():
    request = _request(
        indicators={
            "rsi_14": 50.0,
            "ema_50": 100.0,
            "ema_200": 0.0,
            "macd": {"macd": 1.0, "signal": 0.5, "histogram": 0.5},
            "atr_14": 0.0,
            "volume_sma_20": 90.0,
        }
    )
    context = ContextBuilder().build(request)

    assert context.trend.ema_gap_pct == 0.0
    assert context.momentum.histogram_atr_ratio == 0.0


def test_exit_confirmations_empty_when_bullish():
    request = _request()
    context = ContextBuilder().build(request)
    assert context.exit_confirmations == ()


def test_exit_confirmations_populated_for_bearish_evidence():
    request = _request(
        ohlcv=[
            {"t": "1", "o": 100, "h": 101, "l": 99, "c": 100, "v": 50},
            {"t": "2", "o": 100, "h": 102, "l": 99, "c": 101, "v": 50},
            {"t": "3", "o": 101, "h": 103, "l": 100, "c": 102, "v": 50},
        ],
        indicators={
            "rsi_14": 40.0,
            "ema_50": 95.0,
            "ema_200": 90.0,
            "macd": {"macd": -5.0, "signal": -1.0, "histogram": -1.0},
            "atr_14": 2.0,
            "volume_sma_20": 200.0,
        },
    )
    context = ContextBuilder().build(request)

    assert set(context.exit_confirmations) == {"bearish_macd", "rsi_below_45"}


def test_exit_confirmations_computed_even_without_an_open_position():
    """ContextBuilder reports facts unconditionally; it is the Response
    Validator's job (validators/semantic.py) to decide these only matter
    when a position is actually open."""
    request = _request(
        indicators={
            "rsi_14": 40.0,
            "ema_50": 90.0,
            "ema_200": 100.0,
            "macd": {"macd": -5.0, "signal": -1.0, "histogram": -1.0},
            "atr_14": 2.0,
            "volume_sma_20": 90.0,
        },
        position_context={"has_open_position": False, "unrealized_pnl_pct": None},
    )
    context = ContextBuilder().build(request)

    assert "ema50_below_ema200" in context.exit_confirmations


def test_context_handles_empty_ohlcv_without_raising():
    request = _request(ohlcv=[])
    context = ContextBuilder().build(request)

    assert context.exit_confirmations == ()
    assert context.trend.price_above_ema50 is False
    assert context.volume.latest_above_sma20 is False
