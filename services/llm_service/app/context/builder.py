from ..models.market import (
    MarketContext,
    MomentumMetrics,
    TrendMetrics,
    VolatilityMetrics,
    VolumeMetrics,
)
from ..models.wire import AnalyzeRequest, Candle, Indicators

_RSI_OVERBOUGHT = 70.0
_RSI_OVERSOLD = 30.0
_RSI_MIDPOINT = 50.0


class ContextBuilder:
    """Collects and normalizes every input the rest of the pipeline needs
    into one typed `MarketContext`. Pure and synchronous: it never calls the
    LLM and never decides BUY/SELL/HOLD — that split is the point of this
    refactor (see `services/pipeline.py` for how the stages compose).
    """

    def build(self, request: AnalyzeRequest) -> MarketContext:
        indicators = request.indicators
        candles = request.ohlcv
        latest = candles[-1] if candles else None

        return MarketContext(
            request=request,
            trend=self._trend(latest, indicators),
            momentum=self._momentum(indicators),
            volatility=self._volatility(latest, indicators),
            volume=self._volume(latest, indicators),
            exit_confirmations=self._exit_confirmations(candles, indicators),
        )

    @staticmethod
    def _trend(latest: Candle | None, indicators: Indicators) -> TrendMetrics:
        return TrendMetrics(
            price_above_ema50=latest is not None and latest.c > indicators.ema_50,
            price_above_ema200=latest is not None and latest.c > indicators.ema_200,
            ema50_above_ema200=indicators.ema_50 > indicators.ema_200,
            ema_gap_pct=_safe_ratio(indicators.ema_50 - indicators.ema_200, indicators.ema_200),
        )

    @staticmethod
    def _momentum(indicators: Indicators) -> MomentumMetrics:
        macd = indicators.macd
        return MomentumMetrics(
            macd_bullish=macd.histogram > 0 and macd.macd > macd.signal,
            macd_bearish=macd.histogram < 0 and macd.macd < macd.signal,
            histogram_atr_ratio=_safe_ratio(abs(macd.histogram), indicators.atr_14),
            rsi_zone=_rsi_zone(indicators.rsi_14),
        )

    @staticmethod
    def _volatility(latest: Candle | None, indicators: Indicators) -> VolatilityMetrics:
        price = latest.c if latest is not None else 0.0
        return VolatilityMetrics(atr_pct=_safe_ratio(indicators.atr_14, price))

    @staticmethod
    def _volume(latest: Candle | None, indicators: Indicators) -> VolumeMetrics:
        return VolumeMetrics(
            latest_above_sma20=latest is not None and latest.v > indicators.volume_sma_20
        )

    @staticmethod
    def _exit_confirmations(candles: list[Candle], indicators: Indicators) -> tuple[str, ...]:
        """PROJECT.md Section 8.3's bearish exit confirmations, moved here
        verbatim from the old `semantic_validator.py` so the Response
        Validator no longer recomputes facts the Context Builder already
        owns. Deliberately kept as its own predicate set rather than derived
        from `_trend`/`_momentum` above: e.g. the exit rubric's
        `rsi_14 < 45` threshold is a distinct business rule from the
        descriptive RSI zone boundaries (30/50/70) used for strategy
        framing, not a looser/tighter reading of the same fact.
        """
        if not candles:
            return ()

        latest = candles[-1]
        confirmations: list[str] = []

        if latest.c < indicators.ema_50 and latest.c < indicators.ema_200:
            confirmations.append("price_below_ema50_and_ema200")
        if indicators.ema_50 < indicators.ema_200:
            confirmations.append("ema50_below_ema200")
        if indicators.macd.histogram < 0 and indicators.macd.macd < indicators.macd.signal:
            confirmations.append("bearish_macd")
        if indicators.rsi_14 < 45:
            confirmations.append("rsi_below_45")
        if len(candles) >= 3:
            recent = candles[-3:]
            lower_highs = all(left.h > right.h for left, right in zip(recent, recent[1:]))
            lower_lows = all(left.l > right.l for left, right in zip(recent, recent[1:]))
            if lower_highs and lower_lows:
                confirmations.append("lower_highs_and_lows")
        if len(candles) >= 2 and latest.c < candles[-2].c and latest.v > indicators.volume_sma_20:
            confirmations.append("falling_price_on_high_volume")

        return tuple(confirmations)


def _safe_ratio(numerator: float, denominator: float) -> float:
    """Guards the ratios this module introduces against a zero denominator.
    Real market data never produces one, but Section 14 Rule 3 (fail
    closed, never let an unhandled exception replace a HOLD) means a new
    computation must not be able to turn a would-be-HOLD request into a
    500 instead."""
    return numerator / denominator if denominator else 0.0


def _rsi_zone(rsi: float) -> str:
    if rsi < _RSI_OVERSOLD:
        return "oversold"
    if rsi > _RSI_OVERBOUGHT:
        return "overbought"
    return "bullish_neutral" if rsi >= _RSI_MIDPOINT else "bearish_neutral"
