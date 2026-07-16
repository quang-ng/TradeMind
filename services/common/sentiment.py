from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import SentimentState


class MACDSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    macd: float
    signal: float
    histogram: float


class MarketIndicatorSnapshot(BaseModel):
    """Optional indicator values available for one closed candle.

    Providers decide which fields they require. Missing values therefore
    disable only the affected provider, not the entire sentiment result.
    """

    model_config = ConfigDict(frozen=True)

    price: float | None = Field(default=None, gt=0)
    price_change_pct: float | None = None
    rsi_14: float | None = Field(default=None, ge=0, le=100)
    ema_50: float | None = Field(default=None, gt=0)
    ema_200: float | None = Field(default=None, gt=0)
    macd: MACDSnapshot | None = None
    atr_14: float | None = Field(default=None, ge=0)
    volume: float | None = Field(default=None, ge=0)
    volume_sma_20: float | None = Field(default=None, gt=0)


class IndicatorContribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    indicator: str = Field(min_length=1)
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)


class SentimentWeights(BaseModel):
    """Weights are keyed by provider name so injected providers need no service changes."""

    model_config = ConfigDict(frozen=True)

    values: dict[str, float] = Field(
        default_factory=lambda: {
            "rsi": 1.0,
            "ema_trend": 1.25,
            "macd": 1.0,
            "volatility": 0.75,
            "volume": 0.75,
        }
    )

    @model_validator(mode="after")
    def validate_weights(self) -> "SentimentWeights":
        if any(not name for name in self.values):
            raise ValueError("sentiment weight names must not be empty")
        if any(weight < 0 for weight in self.values.values()):
            raise ValueError("sentiment weights must be non-negative")
        return self


class MarketSentiment(BaseModel):
    """Public advisory result consumed by the LLM and dashboard."""

    model_config = ConfigDict(frozen=True)

    score: int = Field(ge=0, le=100)
    state: SentimentState
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]

    @model_validator(mode="after")
    def state_matches_score(self) -> "MarketSentiment":
        expected = sentiment_state_for_score(self.score)
        if self.state != expected:
            raise ValueError(f"state must be {expected.value} for score {self.score}")
        return self


def sentiment_state_for_score(score: int) -> SentimentState:
    if score <= 30:
        return SentimentState.FEAR
    if score <= 70:
        return SentimentState.NEUTRAL
    return SentimentState.GREED
