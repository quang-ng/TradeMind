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

    Advisory for `prompts/v1.py` (framing context only, never overrides the
    rules) and for every regime but one in `validators/semantic.py`: since
    2026-08-13 a `TREND_FOLLOWING` classification suppresses an otherwise-
    qualifying BUY (walk-forward backtest evidence in that module's
    docstring). The other three regimes still don't branch the rubric.
    Wiring more of them in later is a `PromptBuilder`/`validators` change,
    not an architecture change — the classification itself is already
    real, deterministic, and unit-tested regardless of how much of the
    rubric consumes it.
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
