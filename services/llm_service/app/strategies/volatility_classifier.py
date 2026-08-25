from ..models.market import MarketContext
from ..models.volatility import VolatilityRegime

# Fixed-threshold ATR%-of-price bucketing, same style as
# `selector.py`'s `_TREND_GAP_THRESHOLD_PCT`/`_MOMENTUM_BURST_RATIO`
# (a configured constant, not a historical-percentile computation —
# `ContextBuilder` doesn't currently have that plumbing; positive-
# expectancy plan D2/M2). These sit either side of
# `risk_engine.app.sizing`'s `min_stop_loss_pct`/`max_stop_loss_pct`
# clamp band (1.5%/8%, applied to `atr_14/price * atr_stop_multiplier`,
# i.e. roughly 0.75%-4% of raw ATR% before that multiplier) as a sanity
# anchor, but are defined independently here — `llm_service` never
# imports `risk_engine` (PROJECT.md Section 3/14 rule 1).
_LOW_VOLATILITY_THRESHOLD_PCT = 0.005
_HIGH_VOLATILITY_THRESHOLD_PCT = 0.02


class VolatilityClassifier:
    """Deterministic volatility-regime classifier, same shape as
    `StrategySelector`: makes no LLM call, proposes no BUY/SELL/HOLD, just
    labels the current `MarketContext`'s ATR-relative-to-price bucket."""

    def classify(self, context: MarketContext) -> VolatilityRegime:
        atr_pct = context.volatility.atr_pct
        if atr_pct < _LOW_VOLATILITY_THRESHOLD_PCT:
            return VolatilityRegime.LOW_VOLATILITY
        if atr_pct > _HIGH_VOLATILITY_THRESHOLD_PCT:
            return VolatilityRegime.HIGH_VOLATILITY
        return VolatilityRegime.NORMAL
