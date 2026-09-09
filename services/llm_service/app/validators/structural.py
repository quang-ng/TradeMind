import json

from pydantic import ValidationError

from ..models.llm import LLMOutput
from .llm_contract import check_llm_response


class ValidationFailure(Exception):
    """Raised when a raw LLM response fails the Section 8.3 structural
    validation pipeline."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def parse_llm_response(raw_text: str) -> LLMOutput:
    """Structural half of the PROJECT.md Section 8.3 validation pipeline:
    JSON parse, then the dqflow value contract (`llm_contract.py`: action
    enum, confidence range, non-empty/bounded free text), then `LLMOutput`
    for typing/coercion — in order, first failure raises `ValidationFailure`
    with the failing reason. The semantic exit rubric runs separately, after
    this succeeds (`validators/semantic.py`)."""
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        raise ValidationFailure("malformed_json")

    if not isinstance(data, dict):
        raise ValidationFailure("malformed_json")

    contract_failure = check_llm_response(data)
    if contract_failure is not None:
        raise ValidationFailure(contract_failure)

    try:
        return LLMOutput.model_validate(data)
    except ValidationError as exc:
        raise ValidationFailure(_classify_validation_error(exc))


def _classify_validation_error(exc: ValidationError) -> str:
    for error in exc.errors():
        field = error["loc"][0] if error["loc"] else None
        error_type = error["type"]
        if error_type == "missing":
            continue
        if field == "action":
            return "invalid_action"
        if field == "confidence":
            return "invalid_confidence"
    return "schema_invalid"
