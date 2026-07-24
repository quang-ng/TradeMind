"""Output model for the Context Builder."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .wire import AnalyzeRequest, Candle, Indicators, PositionContext

RsiZone = Literal["oversold", "bearish_neutral", "bullish_neutral", "overbought"]


class TrendMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    price_above_ema50: bool
    price_above_ema200: bool
    ema50_above_ema200: bool
    # (ema_50 - ema_200) / ema_200, signed. Stands in for ADX-style trend
    # strength — this service has no ADX input (the Scheduler does not
    # compute one), so the Strategy Selector proxies "trending vs sideways"
    # off the EMA separation it already has.
    ema_gap_pct: float


class MomentumMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    macd_bullish: bool
    macd_bearish: bool
    # abs(histogram) / atr_14 — a volatility-normalized momentum magnitude,
    # used by the Strategy Selector to flag a momentum burst independent of
    # whether a broader EMA trend has formed yet.
    histogram_atr_ratio: float
    # Mirrors the fixed RSI convention already stated in prompts/v1.py:
    # >70 overbought, <30 oversold, 30-70 neutral (bullish-leaning >=50).
    # Descriptive only — the exit rubric's own `rsi_14 < 45` threshold
    # (see ContextBuilder._exit_confirmations) is a separate business rule,
    # not derived from this zone.
    rsi_zone: RsiZone


class VolatilityMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    atr_pct: float


class VolumeMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    latest_above_sma20: bool


class MarketContext(BaseModel):
    """Strongly typed, normalized view of one `/analyze` request. Built once
    by `ContextBuilder` and read by every later pipeline stage — none of
    which call the LLM or decide BUY/SELL/HOLD from it directly except the
    Response Validator's deterministic exit rubric.

    `request` retains the original wire model so `PromptBuilder` can
    reproduce the exact Section 8.1 JSON the LLM receives byte-for-byte
    (including its `exclude_none` semantics) without re-deriving pydantic
    dump behavior here. The typed fields below are derived facts for the
    Strategy Selector and Response Validator — never sent to the model
    directly.
    """

    model_config = ConfigDict(frozen=True)

    request: AnalyzeRequest
    trend: TrendMetrics
    momentum: MomentumMetrics
    volatility: VolatilityMetrics
    volume: VolumeMetrics
    # PROJECT.md Section 8.3's deterministic bearish exit confirmations,
    # computed unconditionally (whether or not a position is open) — the
    # Response Validator's semantic rubric decides which ones matter.
    exit_confirmations: tuple[str, ...]

    @property
    def symbol(self) -> str:
        return self.request.symbol

    @property
    def timeframe(self) -> str:
        return self.request.timeframe

    @property
    def candle_close_time(self) -> str:
        return self.request.candle_close_time

    @property
    def ohlcv(self) -> list[Candle]:
        return self.request.ohlcv

    @property
    def indicators(self) -> Indicators:
        return self.request.indicators

    @property
    def sentiment(self):
        return self.request.sentiment

    @property
    def position(self) -> PositionContext:
        return self.request.position_context
