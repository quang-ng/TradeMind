from collections.abc import Sequence
from typing import Protocol

from common.sentiment import (
    IndicatorContribution,
    MarketIndicatorSnapshot,
    MarketSentiment,
    SentimentWeights,
    sentiment_state_for_score,
)


class IndicatorProvider(Protocol):
    """Extension point for one deterministic sentiment indicator."""

    @property
    def name(self) -> str: ...

    def evaluate(self, snapshot: MarketIndicatorSnapshot) -> IndicatorContribution | None: ...


class MarketSentimentService:
    """Aggregates independent advisory indicators without external side effects."""

    def __init__(
        self,
        providers: Sequence[IndicatorProvider],
        weights: SentimentWeights | None = None,
    ) -> None:
        self._providers = tuple(providers)
        self._weights = weights or SentimentWeights()
        names = [provider.name for provider in self._providers]
        if len(names) != len(set(names)):
            raise ValueError("sentiment provider names must be unique")

    def classify(self, snapshot: MarketIndicatorSnapshot) -> MarketSentiment:
        weighted_score = 0.0
        effective_weight_total = 0.0
        weighted_confidence = 0.0
        configured_weight_total = sum(
            self._weights.values.get(provider.name, 1.0) for provider in self._providers
        )
        reasons: list[str] = []

        for provider in self._providers:
            weight = self._weights.values.get(provider.name, 1.0)
            if weight == 0:
                continue
            contribution = provider.evaluate(snapshot)
            if contribution is None:
                continue

            effective_weight = weight * contribution.confidence
            weighted_score += contribution.score * effective_weight
            effective_weight_total += effective_weight
            weighted_confidence += contribution.confidence * weight
            reasons.append(contribution.reason)

        if effective_weight_total == 0:
            return MarketSentiment(
                score=50,
                state=sentiment_state_for_score(50),
                confidence=0.0,
                reasons=["No sentiment indicators were available"],
            )

        score = round(weighted_score / effective_weight_total)
        confidence = weighted_confidence / configured_weight_total
        return MarketSentiment(
            score=score,
            state=sentiment_state_for_score(score),
            confidence=round(confidence, 2),
            reasons=reasons,
        )
