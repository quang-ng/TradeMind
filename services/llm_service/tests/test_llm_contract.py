import json

import pandas as pd
import pytest

from llm_service.app.validators.llm_contract import (
    LLM_RESPONSE_CONTRACT,
    check_llm_response,
    contract_failure_reason,
)
from llm_service.app.validators.structural import parse_llm_response

VALID = {
    "action": "SELL",
    "confidence": 0.9,
    "reasoning": "Bearish MACD cross with price rejecting EMA50.",
    "key_indicators": ["macd_cross"],
    "invalidation_condition": "Close back above EMA200.",
}


def test_valid_response_passes_the_contract():
    assert check_llm_response(VALID) is None
    assert LLM_RESPONSE_CONTRACT.validate(pd.DataFrame([VALID])).ok


@pytest.mark.parametrize(
    "mutation,expected_reason",
    [
        ({"action": "MAYBE"}, "invalid_action"),
        ({"action": "buy"}, "invalid_action"),  # case-sensitive enum
        ({"confidence": 1.5}, "invalid_confidence"),
        ({"confidence": -0.1}, "invalid_confidence"),
        ({"confidence": "high"}, "invalid_confidence"),  # dtype check pre-empts min/max
        ({"confidence": None}, "invalid_confidence"),
        ({"reasoning": ""}, "schema_invalid"),
        ({"reasoning": "   "}, "schema_invalid"),
        ({"invalidation_condition": ""}, "schema_invalid"),
        ({"invalidation_condition": "\n\t "}, "schema_invalid"),
    ],
)
def test_field_violations_map_to_the_pipeline_reason(mutation, expected_reason):
    assert check_llm_response({**VALID, **mutation}) == expected_reason


def test_confidence_as_integer_is_accepted():
    # A model that returns `1` rather than `1.0` is fine — dqflow widens
    # integer to a float declaration, and LLMOutput coerces it.
    assert check_llm_response({**VALID, "confidence": 1}) is None
    assert check_llm_response({**VALID, "confidence": 0}) is None


def test_over_long_reasoning_is_truncated_not_rejected():
    # The dqflow contract does not cap length; parse_llm_response trims an
    # overrun to 500 chars (Section 7.1) rather than discarding the signal.
    assert check_llm_response({**VALID, "reasoning": "x" * 800}) is None
    output = parse_llm_response(json.dumps({**VALID, "reasoning": "x" * 800}))
    assert output.reasoning == "x" * 500


@pytest.mark.parametrize(
    "missing", ["action", "confidence", "reasoning", "invalidation_condition"]
)
def test_any_missing_scalar_field_is_schema_invalid(missing):
    assert check_llm_response({k: v for k, v in VALID.items() if k != missing}) == "schema_invalid"


def test_missing_key_indicators_is_accepted():
    # key_indicators is a list field left to LLMOutput (defaults to []), not
    # part of the dqflow contract.
    assert check_llm_response({k: v for k, v in VALID.items() if k != "key_indicators"}) is None


def test_empty_object_is_schema_invalid():
    assert check_llm_response({}) == "schema_invalid"


def test_extra_keys_are_ignored():
    assert check_llm_response({**VALID, "model_confidence_note": "n/a"}) is None


def test_action_failure_wins_over_confidence_failure():
    # Both the action verb and the confidence range are wrong; the action
    # verb is the more specific signal, so it's the reason reported.
    assert check_llm_response({**VALID, "action": "NOPE", "confidence": 9.0}) == "invalid_action"


def test_contract_failure_reason_defaults_to_schema_invalid():
    result = LLM_RESPONSE_CONTRACT.validate(pd.DataFrame([{**VALID, "reasoning": ""}]))
    assert not result.ok
    assert contract_failure_reason(result) == "schema_invalid"
