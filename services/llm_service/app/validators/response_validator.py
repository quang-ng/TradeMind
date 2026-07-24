from collections.abc import Awaitable, Callable

from ..models.llm import ValidationResult
from ..models.market import MarketContext
from .semantic import validate_signal_semantics
from .structural import ValidationFailure, parse_llm_response

# (invalid_raw_text, failure_reason) -> repaired raw text, or None if the
# repair call itself failed to produce anything usable.
RepairFn = Callable[[str, str], Awaitable[str | None]]


class ResponseValidator:
    """Runs PROJECT.md Section 8.3's structural validation pipeline, then
    the position-aware semantic exit rubric. Never fabricates BUY or SELL:
    every path that cannot confidently classify a response returns
    `is_valid=False`, which the caller (`services/pipeline.py`) turns into
    HOLD via the Signal Generator — it never retries by defaulting to a
    trade action.

    `max_repair_attempts=0` (the default) reproduces Section 8.3's
    documented behavior exactly: a malformed/schema-invalid response goes
    straight to HOLD, no retry. Raising it activates the target
    architecture's repair-prompt retry — on a structural failure, the
    caller-supplied `repair` callback is invoked with the bad text and the
    failure reason, and the repaired text is re-validated up to
    `max_repair_attempts` times before giving up. This is real, tested
    behavior, just off by default so this refactor does not, by itself,
    let a previously-guaranteed HOLD become a fabricated trade action.
    """

    def __init__(
        self,
        *,
        min_exit_profit_pct: float = 0.005,
        min_exit_loss_pct: float = 0.005,
        max_repair_attempts: int = 0,
    ):
        self._min_exit_profit_pct = min_exit_profit_pct
        self._min_exit_loss_pct = min_exit_loss_pct
        self._max_repair_attempts = max_repair_attempts

    async def validate(
        self, raw_text: str, context: MarketContext, *, repair: RepairFn | None = None
    ) -> ValidationResult:
        attempt_text = raw_text
        attempts_used = 0
        repair_attempted = False

        while True:
            try:
                output = parse_llm_response(attempt_text)
                break
            except ValidationFailure as exc:
                if attempts_used >= self._max_repair_attempts or repair is None:
                    return ValidationResult(
                        is_valid=False,
                        failure_reason=exc.reason,
                        repair_attempted=repair_attempted,
                        final_raw_text=attempt_text,
                    )
                repaired_text = await repair(attempt_text, exc.reason)
                attempts_used += 1
                repair_attempted = True
                if repaired_text is None:
                    return ValidationResult(
                        is_valid=False,
                        failure_reason=exc.reason,
                        repair_attempted=True,
                        final_raw_text=attempt_text,
                    )
                attempt_text = repaired_text

        semantic_result = validate_signal_semantics(
            context,
            output,
            min_exit_profit_pct=self._min_exit_profit_pct,
            min_exit_loss_pct=self._min_exit_loss_pct,
        )
        return ValidationResult(
            is_valid=True,
            output=semantic_result.output,
            original_action=output.action,
            action_changed=semantic_result.action_changed,
            exit_confirmations=semantic_result.exit_confirmations,
            repair_attempted=repair_attempted,
            final_raw_text=attempt_text,
        )
