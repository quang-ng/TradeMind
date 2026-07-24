"""Internal models for the LLM Client / Response Validator boundary. None of
these cross the `/analyze` HTTP contract (see `models/wire.py`) — they exist
only to pass typed data between pipeline stages."""

from common.enums import Action
from pydantic import BaseModel, ConfigDict, Field


class LLMOutput(BaseModel):
    """Raw model contract, PROJECT.md Section 8.2 — the parsed/validated
    shape of one LLM response, before the semantic exit rubric may still
    normalize `action`/`confidence`/`reasoning` (see
    `validators/semantic.py`)."""

    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1, max_length=500)
    key_indicators: list[str] = Field(default_factory=list)
    invalidation_condition: str = Field(min_length=1)


class LLMRequest(BaseModel):
    """What `PromptBuilder` hands to `LLMClient.generate()`."""

    model_config = ConfigDict(frozen=True)

    system_prompt: str
    user_prompt: str


class LLMResponse(BaseModel):
    """What `LLMClient.generate()` returns: either `raw_text` on success, or
    `failure_reason` (`"provider_error"` / `"llm_timeout"`) on a transport
    failure — never both."""

    model_config = ConfigDict(frozen=True)

    raw_text: str | None
    failure_reason: str | None = None


class ValidationResult(BaseModel):
    """What `ResponseValidator.validate()` returns. `output` is populated
    only when `is_valid` is True; `original_action` preserves what the model
    itself said before the semantic exit rubric may have overridden it (see
    `SemanticValidationResult` in `validators/semantic.py`) so callers can
    still report both for the audit trail."""

    model_config = ConfigDict(frozen=True)

    is_valid: bool
    output: LLMOutput | None = None
    original_action: Action | None = None
    failure_reason: str | None = None
    action_changed: bool = False
    exit_confirmations: tuple[str, ...] = ()
    repair_attempted: bool = False
    final_raw_text: str | None = None
