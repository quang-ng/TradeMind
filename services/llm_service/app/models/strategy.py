"""Output model for the Strategy Selector."""

from enum import Enum

from pydantic import BaseModel, ConfigDict


class StrategyName(str, Enum):
    TREND_FOLLOWING = "trend_following"
    TREND_PULLBACK = "trend_pullback"
    MOMENTUM_CONTINUATION = "momentum_continuation"
    MEAN_REVERSION = "mean_reversion"


class SelectedStrategy(BaseModel):
    """Deterministic regime classification for one `MarketContext`. Purely
    advisory metadata today (see `strategies/selector.py`'s module
    docstring for why it does not yet branch the decision rubric) —
    attached to the final `TradingSignal.raw_response` and, when
    `LLMServiceSettings.include_strategy_context_in_prompt` is enabled,
    surfaced to the model as framing context."""

    model_config = ConfigDict(frozen=True)

    strategy: StrategyName
    possible_alternatives: tuple[StrategyName, ...] = ()
    reasoning: str
