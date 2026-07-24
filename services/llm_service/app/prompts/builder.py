from ..models.llm import LLMRequest
from ..models.market import MarketContext
from ..models.strategy import SelectedStrategy
from .v1 import SYSTEM_PROMPT_V1, build_user_prompt


class PromptBuilder:
    """Assembles the system + user prompt sent to the LLM Client.

    `SYSTEM_PROMPT_V1` already encodes every trading rule, risk constraint,
    and output-schema description this service needs (`prompts/v1.py`) —
    the target architecture's "inject trading rules / risk constraints /
    output schema" is satisfied by that fixed template, not by rebuilding
    it here, per the objective to avoid duplicating reasoning that already
    lives in Python or in the prompt itself. `build()` only ever adds
    strategy-regime framing, and only when explicitly enabled: the exact
    prompt text sent to the LLM must stay byte-for-byte what it was before
    this refactor unless an operator opts in (see
    `LLMServiceSettings.include_strategy_context_in_prompt`), because that
    text drives real trading decisions.
    """

    def __init__(self, *, include_strategy_context: bool = False):
        self._include_strategy_context = include_strategy_context

    def build(self, context: MarketContext, strategy: SelectedStrategy) -> LLMRequest:
        system_prompt = SYSTEM_PROMPT_V1
        if self._include_strategy_context:
            system_prompt = f"{system_prompt}\n\n{_strategy_context_block(strategy)}"
        return LLMRequest(
            system_prompt=system_prompt, user_prompt=build_user_prompt(context.request)
        )

    def build_repair(
        self,
        context: MarketContext,
        strategy: SelectedStrategy,
        invalid_response: str,
        reason: str,
    ) -> LLMRequest:
        """Builds the repair-prompt retry described by the target
        architecture's Response Validator. Only reachable when
        `LLMServiceSettings.max_repair_attempts` > 0 (default 0, see
        `validators/response_validator.py`), so this path is fully
        implemented and unit-tested but inert in production today."""
        base = self.build(context, strategy)
        repair_prompt = (
            f"Your previous response failed validation ({reason}). Previous response:\n"
            f"{invalid_response}\n\n"
            "Re-emit a corrected response for the SAME request below, following the "
            "schema and rules above exactly. Respond with ONLY the corrected JSON "
            "object — no markdown, no commentary.\n\n"
            f"{base.user_prompt}"
        )
        return LLMRequest(system_prompt=base.system_prompt, user_prompt=repair_prompt)


def _strategy_context_block(strategy: SelectedStrategy) -> str:
    return (
        "Additional context (advisory only, does not override the rules above): "
        f"a deterministic pre-classifier flags this as a '{strategy.strategy.value}' "
        f"regime ({strategy.reasoning})"
    )
