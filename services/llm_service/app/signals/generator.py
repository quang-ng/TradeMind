from datetime import UTC, datetime

from common.enums import Action

from ..models.llm import LLMOutput
from ..models.market import MarketContext
from ..models.strategy import SelectedStrategy
from ..models.wire import TradingSignal


class SignalGenerator:
    """Converts a Response Validator outcome into the final `TradingSignal`
    (PROJECT.md Section 7.1). Attaches strategy metadata and a generation
    timestamp inside `raw_response` — never as new top-level fields, since
    `raw_response: dict | None` is already the documented place for
    diagnostic detail the Scheduler stores as-is, while the top-level
    `TradingSignal` shape must stay exactly what PROJECT.md Section 8.2
    defines.

    Enrichment only happens when `raw_response` is already a dict: a total
    provider failure (timeout/`provider_error`) keeps `raw_response=None`
    exactly as before, since turning a `null` into an object there would be
    an observable change for a case this refactor must leave untouched.
    """

    def build_hold(
        self,
        context: MarketContext,
        *,
        reason: str,
        model_name: str,
        strategy: SelectedStrategy | None = None,
        raw_response: dict | None = None,
    ) -> TradingSignal:
        return TradingSignal(
            symbol=context.symbol,
            timeframe=context.timeframe,
            candle_ts=context.candle_close_time,
            action=Action.HOLD,
            confidence=0.0,
            reasoning=reason,
            model_name=model_name,
            raw_response=_enrich(raw_response, strategy),
        )

    def build_signal(
        self,
        context: MarketContext,
        output: LLMOutput,
        *,
        model_name: str,
        strategy: SelectedStrategy | None = None,
        raw_response: dict | None = None,
    ) -> TradingSignal:
        return TradingSignal(
            symbol=context.symbol,
            timeframe=context.timeframe,
            candle_ts=context.candle_close_time,
            action=output.action,
            confidence=output.confidence,
            reasoning=output.reasoning,
            model_name=model_name,
            raw_response=_enrich(raw_response, strategy),
        )


def _enrich(raw_response: dict | None, strategy: SelectedStrategy | None) -> dict | None:
    if raw_response is None or strategy is None:
        return raw_response
    return {
        **raw_response,
        "strategy_selected": strategy.strategy.value,
        "strategy_reasoning": strategy.reasoning,
        "generated_at": datetime.now(UTC).isoformat(),
    }
