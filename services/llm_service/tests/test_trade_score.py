from llm_service.app.context.builder import ContextBuilder
from llm_service.app.models.strategy import SelectedStrategy, StrategyName
from llm_service.app.models.volatility import VolatilityRegime
from llm_service.app.models.wire import AnalyzeRequest
from llm_service.app.scoring.trade_score import TradeScorer


def _context(
    *,
    ema_50: float,
    ema_200: float,
    price: float,
    histogram: float,
    atr_14: float,
    volume: float,
    volume_sma_20: float,
    macd: float | None = None,
    signal: float | None = None,
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


def _strategy(name: StrategyName) -> SelectedStrategy:
    return SelectedStrategy(strategy=name, possible_alternatives=(), reasoning="test")


def test_strong_bullish_setup_scores_high_across_every_component():
    context = _context(
        ema_50=110,
        ema_200=100,
        price=105,  # between the EMAs -> a pullback inside the uptrend
        histogram=1.0,
        atr_14=1.0,  # tight ATR relative to price -> favorable risk:reward
        volume=150,
        volume_sma_20=100,
        macd=2.0,
        signal=1.0,
    )
    strategy = _strategy(StrategyName.TREND_PULLBACK)

    result = TradeScorer().score(context, strategy, VolatilityRegime.NORMAL)

    assert result.total == result.trend + result.momentum + result.volume + (
        result.regime + result.risk_reward + result.volatility
    )
    assert result.trend >= 15  # aligned momentum/uptrend structure present
    assert result.momentum == 20  # bullish + full burst credit
    assert result.volume == 15
    assert result.regime == 20  # TREND_PULLBACK is the highest-scored regime
    assert 0 <= result.risk_reward <= 15
    assert result.volatility == 5  # NORMAL
    assert 0 <= result.total <= 100


def test_weak_setup_scores_low():
    context = _context(
        ema_50=100,
        ema_200=110,  # downtrend structure
        price=95,
        histogram=-1.0,  # bearish MACD
        atr_14=8.0,  # wide ATR relative to price -> poor risk:reward
        volume=5,
        volume_sma_20=100,
        macd=-2.0,
        signal=-1.0,
    )
    strategy = _strategy(StrategyName.TREND_FOLLOWING)

    result = TradeScorer().score(context, strategy, VolatilityRegime.HIGH_VOLATILITY)

    assert result.trend == 0  # not an uptrend
    assert result.momentum == 0  # macd_bearish zeroes the component
    assert result.volume == 0
    assert result.regime == 4  # TREND_FOLLOWING: lowest-scored regime (D2/semantic.py finding)
    assert result.volatility == 1  # HIGH_VOLATILITY
    assert result.total < 30


def test_regime_ranking_matches_the_documented_walk_forward_evidence():
    """`validators/semantic.py`'s 2026-08-13 finding: TREND_FOLLOWING is the
    worst-performing regime of the four and is already suppressed for BUY.
    The Market Regime sub-score must not contradict that by ranking it
    highest."""
    context = _context(
        ema_50=100, ema_200=100, price=100, histogram=0.0, atr_14=2.0, volume=10, volume_sma_20=10
    )
    scorer = TradeScorer()

    scores = {
        name: scorer.score(context, _strategy(name), VolatilityRegime.NORMAL).regime
        for name in StrategyName
    }

    assert scores[StrategyName.TREND_PULLBACK] > scores[StrategyName.MOMENTUM_CONTINUATION]
    assert scores[StrategyName.MOMENTUM_CONTINUATION] > scores[StrategyName.MEAN_REVERSION]
    assert scores[StrategyName.MEAN_REVERSION] > scores[StrategyName.TREND_FOLLOWING]


def test_volatility_component_favors_normal_over_low_and_high():
    context = _context(
        ema_50=100, ema_200=100, price=100, histogram=0.0, atr_14=2.0, volume=10, volume_sma_20=10
    )
    strategy = _strategy(StrategyName.MEAN_REVERSION)
    scorer = TradeScorer()

    normal = scorer.score(context, strategy, VolatilityRegime.NORMAL).volatility
    low = scorer.score(context, strategy, VolatilityRegime.LOW_VOLATILITY).volatility
    high = scorer.score(context, strategy, VolatilityRegime.HIGH_VOLATILITY).volatility

    assert normal > low > high


def test_risk_reward_tightens_as_atr_shrinks_relative_to_price():
    """A tighter ATR-implied stop scores higher (closer to the target
    `assumed_reward_multiple` before the minimal_roi ceiling would already
    have intervened) — see `_score_risk_reward`'s docstring."""
    strategy = _strategy(StrategyName.MEAN_REVERSION)
    scorer = TradeScorer(assumed_reward_multiple=2.0)

    tight = scorer.score(
        _context(
            ema_50=100, ema_200=100, price=1000, histogram=0.0, atr_14=1.0, volume=10,
            volume_sma_20=10,
        ),
        strategy,
        VolatilityRegime.NORMAL,
    )
    wide = scorer.score(
        _context(
            ema_50=100, ema_200=100, price=1000, histogram=0.0, atr_14=100.0, volume=10,
            volume_sma_20=10,
        ),
        strategy,
        VolatilityRegime.NORMAL,
    )

    assert tight.risk_reward > wide.risk_reward
    assert tight.assumed_reward_pct < wide.assumed_reward_pct
    assert tight.assumed_reward_multiple == 2.0


def test_breakdown_matches_component_scores():
    context = _context(
        ema_50=110, ema_200=100, price=112, histogram=0.5, atr_14=2.0, volume=150,
        volume_sma_20=100,
    )
    strategy = _strategy(StrategyName.TREND_FOLLOWING)

    result = TradeScorer().score(context, strategy, VolatilityRegime.NORMAL)
    breakdown = result.breakdown()

    assert breakdown["trend"] == result.trend
    assert breakdown["momentum"] == result.momentum
    assert breakdown["volume"] == result.volume
    assert breakdown["regime"] == result.regime
    assert breakdown["risk_reward"] == result.risk_reward
    assert breakdown["volatility"] == result.volatility
    assert breakdown["assumed_reward_pct"] == result.assumed_reward_pct
