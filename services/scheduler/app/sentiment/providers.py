from dataclasses import dataclass

from common.sentiment import IndicatorContribution, MarketIndicatorSnapshot


@dataclass(frozen=True)
class RSIProvider:
    name: str = "rsi"

    def evaluate(self, snapshot: MarketIndicatorSnapshot) -> IndicatorContribution | None:
        value = snapshot.rsi_14
        if value is None:
            return None
        if value <= 30:
            reason = f"RSI(14) is oversold at {value:.1f}"
        elif value >= 70:
            reason = f"RSI(14) is overbought at {value:.1f}"
        elif value < 45:
            reason = f"RSI(14) shows weak momentum at {value:.1f}"
        elif value > 55:
            reason = f"RSI(14) shows strong momentum at {value:.1f}"
        else:
            reason = f"RSI(14) is neutral at {value:.1f}"
        return IndicatorContribution(
            indicator=self.name, score=value, confidence=0.9, reason=reason
        )


@dataclass(frozen=True)
class EMATrendProvider:
    name: str = "ema_trend"

    def evaluate(self, snapshot: MarketIndicatorSnapshot) -> IndicatorContribution | None:
        price = snapshot.price
        if price is None:
            return None
        available = [("EMA50", snapshot.ema_50), ("EMA200", snapshot.ema_200)]
        available = [(label, value) for label, value in available if value is not None]
        if not available:
            return None

        above = [label for label, value in available if price > value]
        below = [label for label, value in available if price < value]
        confidence = 0.95 if len(available) == 2 else 0.7
        if len(above) == len(available):
            score = 85.0 if len(available) == 2 else 70.0
            reason = f"Price is above {' and '.join(above)}"
        elif len(below) == len(available):
            score = 15.0 if len(available) == 2 else 30.0
            reason = f"Price is below {' and '.join(below)}"
        else:
            score = 50.0
            reason = "Price is between EMA50 and EMA200, indicating a mixed trend"
        return IndicatorContribution(
            indicator=self.name, score=score, confidence=confidence, reason=reason
        )


@dataclass(frozen=True)
class MACDProvider:
    name: str = "macd"

    def evaluate(self, snapshot: MarketIndicatorSnapshot) -> IndicatorContribution | None:
        value = snapshot.macd
        if value is None:
            return None
        scale = max(abs(value.macd), abs(value.signal), 1e-12)
        strength = min(abs(value.histogram) / scale, 1.0)
        confidence = 0.55 + (0.4 * strength)
        if value.histogram > 0:
            score = 70.0 + (20.0 * strength)
            reason = "MACD histogram is positive, indicating bullish momentum"
        elif value.histogram < 0:
            score = 30.0 - (20.0 * strength)
            reason = "MACD histogram is negative, indicating bearish momentum"
        else:
            score = 50.0
            reason = "MACD is flat with no directional momentum"
        return IndicatorContribution(
            indicator=self.name, score=score, confidence=confidence, reason=reason
        )


@dataclass(frozen=True)
class VolatilityProvider:
    high_volatility_pct: float = 0.05
    elevated_volatility_pct: float = 0.03
    normal_volatility_pct: float = 0.01
    name: str = "volatility"

    def evaluate(self, snapshot: MarketIndicatorSnapshot) -> IndicatorContribution | None:
        if snapshot.atr_14 is None or snapshot.price is None:
            return None
        ratio = snapshot.atr_14 / snapshot.price
        if ratio >= self.high_volatility_pct:
            score, reason = 20.0, f"High volatility: ATR is {ratio:.1%} of price"
        elif ratio >= self.elevated_volatility_pct:
            score, reason = 35.0, f"Elevated volatility: ATR is {ratio:.1%} of price"
        elif ratio >= self.normal_volatility_pct:
            score, reason = 50.0, f"Volatility is normal: ATR is {ratio:.1%} of price"
        else:
            score, reason = 60.0, f"Volatility is low: ATR is {ratio:.1%} of price"
        return IndicatorContribution(
            indicator=self.name, score=score, confidence=0.8, reason=reason
        )


@dataclass(frozen=True)
class VolumeProvider:
    high_volume_ratio: float = 1.5
    low_volume_ratio: float = 0.7
    name: str = "volume"

    def evaluate(self, snapshot: MarketIndicatorSnapshot) -> IndicatorContribution | None:
        if snapshot.volume is None or snapshot.volume_sma_20 is None:
            return None
        ratio = snapshot.volume / snapshot.volume_sma_20
        change = snapshot.price_change_pct
        if change is None or abs(change) < 1e-12:
            return IndicatorContribution(
                indicator=self.name,
                score=50.0,
                confidence=0.4,
                reason=f"Volume is {ratio:.2f}x its 20-period average without price direction",
            )

        direction = "rising" if change > 0 else "falling"
        if ratio >= self.high_volume_ratio:
            score = 80.0 if change > 0 else 20.0
            confidence = 0.9
            reason = f"High volume confirms {direction} price action ({ratio:.2f}x average)"
        elif ratio <= self.low_volume_ratio:
            score = 55.0 if change > 0 else 45.0
            confidence = 0.5
            reason = f"Low volume weakens {direction} price action ({ratio:.2f}x average)"
        else:
            score = 65.0 if change > 0 else 35.0
            confidence = 0.65
            reason = f"Normal volume accompanies {direction} price action ({ratio:.2f}x average)"
        return IndicatorContribution(
            indicator=self.name, score=score, confidence=confidence, reason=reason
        )


def default_sentiment_providers() -> tuple[
    RSIProvider, EMATrendProvider, MACDProvider, VolatilityProvider, VolumeProvider
]:
    return (
        RSIProvider(),
        EMATrendProvider(),
        MACDProvider(),
        VolatilityProvider(),
        VolumeProvider(),
    )
