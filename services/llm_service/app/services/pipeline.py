import logging

from ..context.builder import ContextBuilder
from ..llm.client import LLMClient
from ..models.wire import AnalyzeRequest, TradingSignal
from ..prompts.builder import PromptBuilder
from ..signals.generator import SignalGenerator
from ..strategies.selector import StrategySelector
from ..validators.response_validator import ResponseValidator

logger = logging.getLogger(__name__)


class AnalysisPipeline:
    """Composes one `/analyze` call end to end:

        Context Builder -> Strategy Selector -> Prompt Builder ->
        LLM Client -> Response Validator -> Signal Generator

    matching the Scheduler -> ... -> Risk Engine flow in PROJECT.md Section
    8. Every dependency is injected (see `main.py`'s `_build_pipeline`), so
    each stage stays independently unit-testable and this class has no
    logic of its own beyond wiring + the two audit-trail log events that
    used to live inline in `main.py`.
    """

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        strategy_selector: StrategySelector,
        prompt_builder: PromptBuilder,
        llm_client: LLMClient,
        response_validator: ResponseValidator,
        signal_generator: SignalGenerator,
    ):
        self._context_builder = context_builder
        self._strategy_selector = strategy_selector
        self._prompt_builder = prompt_builder
        self._llm_client = llm_client
        self._response_validator = response_validator
        self._signal_generator = signal_generator

    async def run(self, request: AnalyzeRequest) -> TradingSignal:
        model_name = self._llm_client.model_name
        context = self._context_builder.build(request)
        strategy = self._strategy_selector.select(context)

        llm_request = self._prompt_builder.build(context, strategy)
        llm_response = await self._llm_client.generate(llm_request)

        if llm_response.failure_reason is not None:
            logger.warning(
                "llm_call_failed",
                extra={"reason": llm_response.failure_reason, "symbol": context.symbol},
            )
            return self._signal_generator.build_hold(
                context,
                reason=llm_response.failure_reason,
                model_name=model_name,
                strategy=strategy,
            )

        async def _repair(bad_text: str, reason: str) -> str | None:
            repair_request = self._prompt_builder.build_repair(context, strategy, bad_text, reason)
            repaired = await self._llm_client.generate(repair_request)
            return repaired.raw_text if repaired.failure_reason is None else None

        validation = await self._response_validator.validate(
            llm_response.raw_text, context, repair=_repair
        )

        if not validation.is_valid:
            logger.warning(
                "llm_response_invalid",
                extra={"reason": validation.failure_reason, "symbol": context.symbol},
            )
            return self._signal_generator.build_hold(
                context,
                reason=validation.failure_reason,
                model_name=model_name,
                strategy=strategy,
                raw_response={"raw": validation.final_raw_text},
            )

        raw_response = {
            "raw": validation.final_raw_text,
            "model_action": validation.original_action.value,
            "semantic_action": validation.output.action.value,
            "exit_confirmations": list(validation.exit_confirmations),
        }
        if validation.action_changed:
            logger.info(
                "llm_action_semantically_normalized",
                extra={
                    "symbol": context.symbol,
                    "model_action": validation.original_action.value,
                    "semantic_action": validation.output.action.value,
                    "exit_confirmations": list(validation.exit_confirmations),
                },
            )
        return self._signal_generator.build_signal(
            context,
            validation.output,
            model_name=model_name,
            strategy=strategy,
            raw_response=raw_response,
        )
