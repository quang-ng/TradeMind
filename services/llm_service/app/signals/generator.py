from datetime import UTC, datetime

from common.enums import Action

from ..models.llm import LLMOutput
from ..models.market import MarketContext
from ..models.strategy import SelectedStrategy
from ..models.volatility import VolatilityRegime
from ..models.wire import TradingSignal
from ..scoring.trade_score import TradeScoreResult


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

    `trade_score`/`score_breakdown`/`setup_regime`/`volatility_regime`
    (positive-expectancy plan D3/M2) are the opposite of that: first-class
    `TradingSignal` fields, not folded into `raw_response`, set whenever
    `score`/`volatility_regime` are supplied regardless of `raw_response`'s
    own state — computed once per cycle in `AnalysisPipeline` before the LLM
    call, so every signal (including HOLD/failure paths) carries them.
    """

    def build_hold(
        self,
        context: MarketContext,
        *,
        reason: str,
        model_name: str,
        strategy: SelectedStrategy | None = None,
        raw_response: dict | None = None,
        volatility_regime: VolatilityRegime | None = None,
        score: TradeScoreResult | None = None,
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
            **_journal_fields(strategy, volatility_regime, score),
        )

    def build_signal(
        self,
        context: MarketContext,
        output: LLMOutput,
        *,
        model_name: str,
        strategy: SelectedStrategy | None = None,
        raw_response: dict | None = None,
        volatility_regime: VolatilityRegime | None = None,
        score: TradeScoreResult | None = None,
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
            **_journal_fields(strategy, volatility_regime, score),
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


def _journal_fields(
    strategy: SelectedStrategy | None,
    volatility_regime: VolatilityRegime | None,
    score: TradeScoreResult | None,
) -> dict:
    return {
        "trade_score": score.total if score is not None else None,
        "score_breakdown": score.breakdown() if score is not None else None,
        "setup_regime": strategy.strategy.value if strategy is not None else None,
        "volatility_regime": volatility_regime.value if volatility_regime is not None else None,
    }
