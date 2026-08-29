"""Output model for the Volatility Classifier.

Positive-expectancy plan D2: the *existing* `StrategySelector` taxonomy
(`models/strategy.py`) already covers trend/momentum regime; this is the one
genuinely missing, orthogonal dimension — ATR today is only ever used for
stop-sizing (`risk_engine/app/sizing.py`), never classified into its own
bucket. Kept as a separate enum/module rather than folded into
`StrategyName` because the two are independent axes: a `MEAN_REVERSION`
setup can be `HIGH_VOLATILITY` or `LOW_VOLATILITY` just as easily as a
`TREND_FOLLOWING` one can.
"""

from enum import Enum


class VolatilityRegime(str, Enum):
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    NORMAL = "NORMAL"
    LOW_VOLATILITY = "LOW_VOLATILITY"
