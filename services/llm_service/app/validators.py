import json

from common.enums import Action
from pydantic import ValidationError

from .schemas import AnalyzeRequest, LLMOutput, Signal


class ValidationFailure(Exception):
    """Raised when a raw LLM response fails the Section 8.3 validation pipeline."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def parse_llm_response(raw_text: str) -> LLMOutput:
    """Validation pipeline (PROJECT.md Section 8.3): JSON parse, schema
    conformance, action enum, confidence range, reasoning length — in order,
    first failure raises ValidationFailure with the failing reason."""
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        raise ValidationFailure("malformed_json")

    if not isinstance(data, dict):
        raise ValidationFailure("malformed_json")

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


def build_hold_signal(
    request: AnalyzeRequest,
    reason: str,
    model_name: str,
    raw_response: dict | None = None,
) -> Signal:
    return Signal(
        symbol=request.symbol,
        timeframe=request.timeframe,
        candle_ts=request.candle_close_time,
        action=Action.HOLD,
        confidence=0.0,
        reasoning=reason,
        model_name=model_name,
        raw_response=raw_response,
    )


def build_signal(
    request: AnalyzeRequest,
    output: LLMOutput,
    model_name: str,
    raw_response: dict | None = None,
) -> Signal:
    return Signal(
        symbol=request.symbol,
        timeframe=request.timeframe,
        candle_ts=request.candle_close_time,
        action=output.action,
        confidence=output.confidence,
        reasoning=output.reasoning,
        model_name=model_name,
        raw_response=raw_response,
    )
