"""Deterministic Trade Score (positive-expectancy plan D3/M2, vision doc
Phase 5): a 0-100 rubric describing setup *quality*, independent of what the
LLM ultimately decides. Computed from the same `MarketContext`/
`SelectedStrategy` the Strategy Selector already has by the time
`AnalysisPipeline` runs — same trust-zone boundary, same "descriptive only,
zero execution authority, no sizing" constraint (PROJECT.md Section 3/14
rule 1: `llm_service` must never import from `risk_engine`).

Rubric (vision doc Phase 5): Trend 0-25 / Momentum 0-20 / Volume 0-15 /
Market Regime 0-20 / Risk:Reward 0-15 / Volatility 0-5 = 0-100 total. Each
sub-score is its own small pure function, same shape/testability as
`StrategySelector`.
"""

from dataclasses import dataclass

from ..models.market import (
    MarketContext,
    MomentumMetrics,
    TrendMetrics,
    VolatilityMetrics,
    VolumeMetrics,
)
from ..models.strategy import SelectedStrategy, StrategyName
from ..models.volatility import VolatilityRegime


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


@dataclass(frozen=True)
class TradeScoreResult:
    total: int
    trend: int
    momentum: int
    volume: int
    regime: int
    risk_reward: int
    volatility: int
    # Carried through for audit visibility (Section 5/`score_breakdown`),
    # not itself part of the score shape — see `_score_risk_reward`.
    assumed_reward_pct: float
    assumed_reward_multiple: float

    def breakdown(self) -> dict[str, float]:
        """`Signal.score_breakdown` (JSONB) — per-component detail for
        audit/debugging, distinct from the single `Signal.trade_score`
        total column."""
        return {
            "trend": self.trend,
            "momentum": self.momentum,
            "volume": self.volume,
            "regime": self.regime,
            "risk_reward": self.risk_reward,
            "volatility": self.volatility,
            "assumed_reward_pct": self.assumed_reward_pct,
            "assumed_reward_multiple": self.assumed_reward_multiple,
        }


# --- Trend (0-25) -----------------------------------------------------
# Long-only MVP (PROJECT.md Section 9.2: no short side) — every sub-score
# below rewards bullish alignment specifically, not "trendiness" in either
# direction. A downtrend or flat market scores 0 here, same as the Strategy
# Selector's own directional read of `ema_gap_pct`.
_TREND_ALIGNED_PTS = 10
_TREND_STRUCTURE_PTS = 10
_TREND_STRENGTH_MAX_PTS = 5
# Full strength credit at 2x the Strategy Selector's own "is this actually
# trending" threshold (`selector.py::_TREND_GAP_THRESHOLD_PCT`) — a gap that
# barely clears the trending bar shouldn't also max out the strength score.
_TREND_STRENGTH_FULL_GAP_PCT = 0.03


def _score_trend(trend: TrendMetrics) -> int:
    is_uptrend = trend.ema_gap_pct > 0
    score = 0
    if is_uptrend and trend.price_above_ema50:
        score += _TREND_ALIGNED_PTS
    if is_uptrend and trend.price_above_ema200 and trend.ema50_above_ema200:
        score += _TREND_STRUCTURE_PTS
    if is_uptrend:
        strength_ratio = _clamp(trend.ema_gap_pct / _TREND_STRENGTH_FULL_GAP_PCT, 0.0, 1.0)
        score += round(strength_ratio * _TREND_STRENGTH_MAX_PTS)
    return score


# --- Momentum (0-20) ---------------------------------------------------
_MOMENTUM_DIRECTION_PTS = 12
_MOMENTUM_BURST_MAX_PTS = 8
# Mirrors `selector.py::_MOMENTUM_BURST_RATIO` (0.15). Duplicated rather
# than imported so `scoring/` stays independently testable without reaching
# into another package's private constant.
_MOMENTUM_BURST_RATIO = 0.15


def _score_momentum(momentum: MomentumMetrics) -> int:
    if momentum.macd_bearish:
        return 0
    direction_score = (
        _MOMENTUM_DIRECTION_PTS if momentum.macd_bullish else _MOMENTUM_DIRECTION_PTS // 2
    )
    burst_ratio = _clamp(momentum.histogram_atr_ratio / _MOMENTUM_BURST_RATIO, 0.0, 1.0)
    return direction_score + round(burst_ratio * _MOMENTUM_BURST_MAX_PTS)


# --- Volume (0-15) ------------------------------------------------------
# `VolumeMetrics` carries exactly one deterministic fact today
# (`latest_above_sma20`); no finer granularity to score against without
# inventing data the Context Builder doesn't compute (YAGNI).
_VOLUME_ABOVE_SMA_PTS = 15


def _score_volume(volume: VolumeMetrics) -> int:
    return _VOLUME_ABOVE_SMA_PTS if volume.latest_above_sma20 else 0


# --- Market Regime (0-20) -----------------------------------------------
# Not a flat "any regime is fine" mapping: `validators/semantic.py`'s
# 2026-08-13 walk-forward finding (`--suppress-buy-regimes trend_following`)
# is that TREND_FOLLOWING is the worst-performing of the four regimes and is
# already suppressed for BUY entirely. This rubric reflects the same
# evidence rather than contradicting it — TREND_FOLLOWING scores low (not
# zero, since the classification is still evidence of *some* trend
# structure), TREND_PULLBACK highest (the freshest, least-chased entry into
# an already-confirmed trend).
_REGIME_SCORES: dict[StrategyName, int] = {
    StrategyName.TREND_PULLBACK: 20,
    StrategyName.MOMENTUM_CONTINUATION: 16,
    StrategyName.MEAN_REVERSION: 10,
    StrategyName.TREND_FOLLOWING: 4,
}


def _score_regime(strategy: SelectedStrategy) -> int:
    return _REGIME_SCORES[strategy.strategy]


# --- Risk:Reward (0-15) --------------------------------------------------
# Self-contained, llm_service-local approximation (D3) — deliberately NOT
# `risk_engine.app.sizing`'s real ATR-stop formula, which this service must
# never import (PROJECT.md Section 3/14 rule 1). These constants mirror the
# *shape* of the public Section 9.2 formula but are free to drift from
# risk_engine's actual values; that's an accepted trade-off for keeping the
# trust-zone boundary intact.
_ASSUMED_ATR_STOP_MULTIPLIER = 2.0
_ASSUMED_MIN_STOP_LOSS_PCT = 0.015
_ASSUMED_MAX_STOP_LOSS_PCT = 0.08
_RISK_REWARD_MAX_PTS = 15
_RISK_REWARD_FLOOR_PTS = 5


def _assumed_stop_distance_pct(atr_pct: float) -> float:
    return _clamp(
        atr_pct * _ASSUMED_ATR_STOP_MULTIPLIER,
        _ASSUMED_MIN_STOP_LOSS_PCT,
        _ASSUMED_MAX_STOP_LOSS_PCT,
    )


def _score_risk_reward(
    volatility: VolatilityMetrics, *, assumed_reward_multiple: float
) -> tuple[int, float]:
    """A tight, unclamped stop (near the floor of the assumed band) means
    the configured `assumed_reward_multiple` reward target is a small move
    relative to typical noise — more achievable before the `minimal_roi`
    safety net's own decaying ceiling (PROJECT.md Section 9.2) would
    otherwise intervene first. A stop pinned to the ceiling means ATR is
    wide enough that reaching the same multiple is a much bigger ask.
    Score tapers linearly between the two; never below the floor, since a
    wide stop is a worse setup, not a disqualifying one."""
    stop_distance_pct = _assumed_stop_distance_pct(volatility.atr_pct)
    band = _ASSUMED_MAX_STOP_LOSS_PCT - _ASSUMED_MIN_STOP_LOSS_PCT
    tightness_ratio = 1.0 - _clamp(
        (stop_distance_pct - _ASSUMED_MIN_STOP_LOSS_PCT) / band, 0.0, 1.0
    )
    score_range = _RISK_REWARD_MAX_PTS - _RISK_REWARD_FLOOR_PTS
    score = _RISK_REWARD_FLOOR_PTS + round(tightness_ratio * score_range)
    assumed_reward_pct = stop_distance_pct * assumed_reward_multiple
    return score, assumed_reward_pct


# --- Volatility (0-5) -----------------------------------------------------
# NORMAL scores highest: LOW means little opportunity (small moves, thin
# reward before fees/slippage dominate); HIGH means unreliable stop
# placement/execution slippage (same "wide stop" concern as risk:reward
# above, scored separately here since it's about *regime label* consistency
# with `Signal.volatility_regime`, not the raw ATR% figure).
_VOLATILITY_SCORES: dict[VolatilityRegime, int] = {
    VolatilityRegime.NORMAL: 5,
    VolatilityRegime.LOW_VOLATILITY: 3,
    VolatilityRegime.HIGH_VOLATILITY: 1,
}


def _score_volatility(regime: VolatilityRegime) -> int:
    return _VOLATILITY_SCORES[regime]


class TradeScorer:
    """`TradeScorer.score(context, strategy, volatility_regime) ->
    TradeScoreResult`. Takes `volatility_regime` as an explicit parameter
    (rather than computing it internally via `VolatilityClassifier`, one
    reading of the plan's `score(context, strategy)` sketch) so the same
    already-computed value backs both `Signal.volatility_regime` and this
    score's Volatility sub-component — no duplicate classification, no
    hidden collaborator."""

    def __init__(self, *, assumed_reward_multiple: float = 2.0):
        self._assumed_reward_multiple = assumed_reward_multiple

    def score(
        self,
        context: MarketContext,
        strategy: SelectedStrategy,
        volatility_regime: VolatilityRegime,
    ) -> TradeScoreResult:
        trend = _score_trend(context.trend)
        momentum = _score_momentum(context.momentum)
        volume = _score_volume(context.volume)
        regime = _score_regime(strategy)
        risk_reward, assumed_reward_pct = _score_risk_reward(
            context.volatility, assumed_reward_multiple=self._assumed_reward_multiple
        )
        volatility = _score_volatility(volatility_regime)
        total = trend + momentum + volume + regime + risk_reward + volatility
        return TradeScoreResult(
            total=total,
            trend=trend,
            momentum=momentum,
            volume=volume,
            regime=regime,
            risk_reward=risk_reward,
            volatility=volatility,
            assumed_reward_pct=assumed_reward_pct,
            assumed_reward_multiple=self._assumed_reward_multiple,
        )
