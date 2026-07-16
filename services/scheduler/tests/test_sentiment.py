from dataclasses import dataclass

import pytest
from common.enums import SentimentState
from common.sentiment import (
    IndicatorContribution,
    MACDSnapshot,
    MarketIndicatorSnapshot,
    MarketSentiment,
    SentimentWeights,
    sentiment_state_for_score,
)
from pydantic import ValidationError
from scheduler.app.sentiment import MarketSentimentService, default_sentiment_providers
from scheduler.app.sentiment.providers import (
    EMATrendProvider,
    MACDProvider,
    RSIProvider,
    VolatilityProvider,
    VolumeProvider,
)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, SentimentState.FEAR),
        (30, SentimentState.FEAR),
        (31, SentimentState.NEUTRAL),
        (70, SentimentState.NEUTRAL),
        (71, SentimentState.GREED),
        (100, SentimentState.GREED),
    ],
)
def test_sentiment_state_boundaries(score: int, expected: SentimentState):
    assert sentiment_state_for_score(score) == expected


def test_market_sentiment_rejects_state_that_does_not_match_score():
    with pytest.raises(ValidationError):
        MarketSentiment(score=25, state=SentimentState.GREED, confidence=0.8, reasons=["x"])


def test_rsi_provider_generates_human_readable_oversold_reason():
    contribution = RSIProvider().evaluate(MarketIndicatorSnapshot(rsi_14=25))

    assert contribution is not None
    assert contribution.score == 25
    assert contribution.confidence == 0.9
    assert contribution.reason == "RSI(14) is oversold at 25.0"


def test_ema_provider_scores_price_below_both_averages_as_fearful():
    contribution = EMATrendProvider().evaluate(
        MarketIndicatorSnapshot(price=90, ema_50=100, ema_200=110)
    )

    assert contribution is not None
    assert contribution.score == 15
    assert contribution.confidence == 0.95
    assert contribution.reason == "Price is below EMA50 and EMA200"


def test_macd_provider_scores_positive_histogram_as_greedy():
    contribution = MACDProvider().evaluate(
        MarketIndicatorSnapshot(macd=MACDSnapshot(macd=4, signal=2, histogram=2))
    )

    assert contribution is not None
    assert contribution.score == 80
    assert contribution.confidence == pytest.approx(0.75)
    assert "bullish momentum" in contribution.reason


def test_volatility_provider_normalizes_atr_by_price():
    contribution = VolatilityProvider().evaluate(MarketIndicatorSnapshot(price=100, atr_14=6))

    assert contribution is not None
    assert contribution.score == 20
    assert contribution.reason == "High volatility: ATR is 6.0% of price"


def test_volume_provider_uses_price_direction_to_interpret_high_volume():
    contribution = VolumeProvider().evaluate(
        MarketIndicatorSnapshot(volume=200, volume_sma_20=100, price_change_pct=-0.03)
    )

    assert contribution is not None
    assert contribution.score == 20
    assert contribution.confidence == 0.9
    assert contribution.reason == "High volume confirms falling price action (2.00x average)"


def test_default_service_returns_fear_with_all_bearish_indicators():
    snapshot = MarketIndicatorSnapshot(
        price=90,
        price_change_pct=-0.04,
        rsi_14=20,
        ema_50=100,
        ema_200=110,
        macd=MACDSnapshot(macd=-4, signal=-2, histogram=-2),
        atr_14=6,
        volume=200,
        volume_sma_20=100,
    )

    result = MarketSentimentService(default_sentiment_providers()).classify(snapshot)

    assert result.state == SentimentState.FEAR
    assert 0 <= result.score <= 30
    assert result.confidence == 0.87
    assert len(result.reasons) == 5
    assert all(isinstance(reason, str) and reason for reason in result.reasons)


def test_missing_indicators_are_skipped_without_failing_the_result():
    result = MarketSentimentService(default_sentiment_providers()).classify(
        MarketIndicatorSnapshot(rsi_14=80)
    )

    assert result.score == 80
    assert result.state == SentimentState.GREED
    assert result.confidence == 0.19
    assert result.reasons == ["RSI(14) is overbought at 80.0"]


def test_all_missing_indicators_return_low_confidence_neutral():
    result = MarketSentimentService(default_sentiment_providers()).classify(
        MarketIndicatorSnapshot()
    )

    assert result == MarketSentiment(
        score=50,
        state=SentimentState.NEUTRAL,
        confidence=0.0,
        reasons=["No sentiment indicators were available"],
    )


@dataclass(frozen=True)
class FixedProvider:
    name: str
    score: float
    confidence: float

    def evaluate(self, snapshot: MarketIndicatorSnapshot) -> IndicatorContribution:
        return IndicatorContribution(
            indicator=self.name,
            score=self.score,
            confidence=self.confidence,
            reason=f"{self.name} reason",
        )


def test_configurable_weights_and_injected_provider_extension():
    service = MarketSentimentService(
        providers=(FixedProvider("fear", 0, 1), FixedProvider("custom_greed", 100, 1)),
        weights=SentimentWeights(values={"fear": 1, "custom_greed": 3}),
    )

    result = service.classify(MarketIndicatorSnapshot())

    assert result.score == 75
    assert result.state == SentimentState.GREED
    assert result.reasons == ["fear reason", "custom_greed reason"]


def test_provider_confidence_reduces_its_influence_on_score():
    service = MarketSentimentService(
        providers=(FixedProvider("certain_fear", 0, 1), FixedProvider("uncertain_greed", 100, 0.1)),
        weights=SentimentWeights(values={"certain_fear": 1, "uncertain_greed": 1}),
    )

    result = service.classify(MarketIndicatorSnapshot())

    assert result.score == 9
    assert result.confidence == 0.55


def test_negative_weights_are_rejected():
    with pytest.raises(ValidationError):
        SentimentWeights(values={"rsi": -1})
