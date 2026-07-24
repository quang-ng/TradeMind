from llm_service.app.context.builder import ContextBuilder
from llm_service.app.models.strategy import StrategyName
from llm_service.app.models.wire import AnalyzeRequest
from llm_service.app.strategies.selector import StrategySelector


def _context(
    *,
    ema_50,
    ema_200,
    price,
    histogram,
    atr_14,
    volume,
    volume_sma_20,
    macd=None,
    signal=None,
):
    macd_val = histogram if macd is None else macd
    signal_val = 0.0 if signal is None else signal
    request = AnalyzeRequest.model_validate(
        {
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "candle_close_time": "2026-07-15T13:00:00Z",
            "ohlcv": [{"t": "1", "o": price, "h": price, "l": price, "c": price, "v": volume}],
            "indicators": {
                "rsi_14": 50.0,
                "ema_50": ema_50,
                "ema_200": ema_200,
                "macd": {"macd": macd_val, "signal": signal_val, "histogram": histogram},
                "atr_14": atr_14,
                "volume_sma_20": volume_sma_20,
            },
            "position_context": {"has_open_position": False, "unrealized_pnl_pct": None},
        }
    )
    return ContextBuilder().build(request)


def test_selects_trend_following_for_a_large_ema_gap_aligned_with_price():
    context = _context(
        ema_50=110, ema_200=100, price=115, histogram=0.1, atr_14=2.0, volume=10, volume_sma_20=100
    )

    result = StrategySelector().select(context)

    assert result.strategy == StrategyName.TREND_FOLLOWING
    assert result.possible_alternatives == ()
    assert "uptrend" in result.reasoning


def test_selects_trend_pullback_when_price_sits_between_the_emas_in_a_trend():
    context = _context(
        ema_50=110, ema_200=100, price=105, histogram=0.1, atr_14=2.0, volume=10, volume_sma_20=100
    )

    result = StrategySelector().select(context)

    assert result.strategy == StrategyName.TREND_PULLBACK
    assert "pullback" in result.reasoning


def test_selects_momentum_continuation_for_a_volume_confirmed_macd_burst_without_a_trend():
    context = _context(
        ema_50=100.5,
        ema_200=100,
        price=101,
        histogram=1.0,
        atr_14=2.0,
        volume=150,
        volume_sma_20=100,
        macd=2.0,
        signal=1.0,
    )

    result = StrategySelector().select(context)

    assert result.strategy == StrategyName.MOMENTUM_CONTINUATION
    assert "bullish" in result.reasoning


def test_falls_back_to_mean_reversion_when_sideways_and_no_momentum_burst():
    context = _context(
        ema_50=100.5,
        ema_200=100,
        price=100.4,
        histogram=0.05,
        atr_14=2.0,
        volume=10,
        volume_sma_20=100,
    )

    result = StrategySelector().select(context)

    assert result.strategy == StrategyName.MEAN_REVERSION
    assert result.possible_alternatives == ()


def test_lists_momentum_as_an_alternative_alongside_a_trending_primary():
    context = _context(
        ema_50=110,
        ema_200=100,
        price=115,
        histogram=1.0,
        atr_14=2.0,
        volume=150,
        volume_sma_20=100,
        macd=2.0,
        signal=1.0,
    )

    result = StrategySelector().select(context)

    assert result.strategy == StrategyName.TREND_FOLLOWING
    assert result.possible_alternatives == (StrategyName.MOMENTUM_CONTINUATION,)


def test_selection_never_calls_the_llm_or_proposes_an_action():
    """StrategySelector's public surface has no async method and its result
    type has no action/confidence field — this is a static assertion that
    the contract stays that way."""
    context = _context(
        ema_50=110, ema_200=100, price=115, histogram=0.1, atr_14=2.0, volume=10, volume_sma_20=100
    )
    result = StrategySelector().select(context)

    assert not hasattr(result, "action")
    assert not hasattr(result, "confidence")
