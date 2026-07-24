from ..models.market import MarketContext
from ..models.strategy import SelectedStrategy, StrategyName

# EMA50/EMA200 separation used as an ADX-style trend-strength proxy — this
# service has no ADX input, so "is the market trending" is read off the
# indicators it already has (PROJECT.md Section 8.1's `indicators` block).
_TREND_GAP_THRESHOLD_PCT = 0.015
# abs(MACD histogram) / ATR(14) at or above this ratio, with volume
# confirming, is treated as a momentum burst independent of trend state.
_MOMENTUM_BURST_RATIO = 0.15


class StrategySelector:
    """Deterministic regime classifier (PROJECT.md's target architecture,
    "Strategy Selector"). Makes no LLM call and proposes no BUY/SELL/HOLD —
    it only labels which of a fixed set of named strategies best describes
    the current `MarketContext`, plus any runner-up regimes and why.

    Deliberately advisory for now: the actual decision rubric enforced by
    `prompts/v1.py` and `validators/semantic.py` does not yet branch on
    `strategy`. See the module-level note in `services/pipeline.py` and the
    migration notes for why — in short, branching the rubric per strategy
    would change today's trading decisions, which this refactor is
    explicitly not allowed to do. The classification is still real,
    deterministic, and unit-tested; wiring a strategy-specific rubric later
    is a `PromptBuilder`/`validators` change, not an architecture change.
    """

    def select(self, context: MarketContext) -> SelectedStrategy:
        trend = context.trend
        momentum = context.momentum
        volume = context.volume

        is_trending = abs(trend.ema_gap_pct) >= _TREND_GAP_THRESHOLD_PCT
        is_uptrend = trend.ema_gap_pct > 0
        aligned_with_trend = (
            trend.price_above_ema50 and trend.price_above_ema200
            if is_uptrend
            else not trend.price_above_ema50 and not trend.price_above_ema200
        )
        is_pullback = is_trending and not aligned_with_trend
        is_momentum_burst = (
            momentum.histogram_atr_ratio >= _MOMENTUM_BURST_RATIO and volume.latest_above_sma20
        )

        ranked: list[tuple[StrategyName, str]] = []

        if is_pullback:
            direction = "uptrend" if is_uptrend else "downtrend"
            ranked.append((
                StrategyName.TREND_PULLBACK,
                f"EMA50/EMA200 gap of {trend.ema_gap_pct:.2%} confirms an underlying "
                f"{direction}, but price sits on the opposite side of EMA50 from that "
                "trend: a pullback inside it.",
            ))
        elif is_trending:
            direction = "uptrend" if is_uptrend else "downtrend"
            ranked.append((
                StrategyName.TREND_FOLLOWING,
                f"EMA50/EMA200 gap of {trend.ema_gap_pct:.2%} exceeds the "
                f"{_TREND_GAP_THRESHOLD_PCT:.2%} trend threshold and price is aligned "
                f"with the {direction}.",
            ))

        if is_momentum_burst:
            if momentum.macd_bullish:
                direction = "bullish"
            elif momentum.macd_bearish:
                direction = "bearish"
            else:
                direction = "mixed"
            ranked.append((
                StrategyName.MOMENTUM_CONTINUATION,
                f"MACD histogram is {momentum.histogram_atr_ratio:.2f}x ATR(14) with "
                f"volume above its 20-period average: a {direction} momentum burst.",
            ))

        if not ranked:
            ranked.append((
                StrategyName.MEAN_REVERSION,
                f"EMA50/EMA200 gap of {trend.ema_gap_pct:.2%} is inside the "
                f"{_TREND_GAP_THRESHOLD_PCT:.2%} trend threshold and no momentum burst "
                "is present: a sideways/ranging regime.",
            ))

        primary_strategy, primary_reasoning = ranked[0]
        alternatives = tuple(name for name, _ in ranked[1:])
        return SelectedStrategy(
            strategy=primary_strategy,
            possible_alternatives=alternatives,
            reasoning=primary_reasoning,
        )
