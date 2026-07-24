import json

import pytest
from common.enums import Action
from llm_service.app.validators.structural import ValidationFailure, parse_llm_response

VALID_RESPONSE = json.dumps(
    {
        "action": "SELL",
        "confidence": 0.9,
        "reasoning": "Strong bearish divergence on the 1h chart.",
        "key_indicators": ["macd_cross"],
        "invalidation_condition": "Close above EMA200.",
    }
)


def test_parse_llm_response_accepts_valid_output():
    output = parse_llm_response(VALID_RESPONSE)

    assert output.action == Action.SELL
    assert output.confidence == 0.9
    assert output.key_indicators == ["macd_cross"]


@pytest.mark.parametrize(
    "raw_text,expected_reason",
    [
        ("not valid json {{{", "malformed_json"),
        ("[1, 2, 3]", "malformed_json"),
        ('"just a string"', "malformed_json"),
        (
            json.dumps(
                {
                    "confidence": 0.5,
                    "reasoning": "x",
                    "key_indicators": [],
                    "invalidation_condition": "x",
                }
            ),
            "schema_invalid",
        ),
        (
            json.dumps(
                {
                    "action": "MAYBE",
                    "confidence": 0.5,
                    "reasoning": "x",
                    "key_indicators": [],
                    "invalidation_condition": "x",
                }
            ),
            "invalid_action",
        ),
        (
            json.dumps(
                {
                    "action": "BUY",
                    "confidence": 1.5,
                    "reasoning": "x",
                    "key_indicators": [],
                    "invalidation_condition": "x",
                }
            ),
            "invalid_confidence",
        ),
        (
            json.dumps(
                {
                    "action": "BUY",
                    "confidence": -0.1,
                    "reasoning": "x",
                    "key_indicators": [],
                    "invalidation_condition": "x",
                }
            ),
            "invalid_confidence",
        ),
        (
            json.dumps(
                {
                    "action": "BUY",
                    "confidence": 0.5,
                    "reasoning": "",
                    "key_indicators": [],
                    "invalidation_condition": "x",
                }
            ),
            "schema_invalid",
        ),
    ],
)
def test_parse_llm_response_rejects_invalid_output(raw_text, expected_reason):
    with pytest.raises(ValidationFailure) as exc_info:
        parse_llm_response(raw_text)

    assert exc_info.value.reason == expected_reason
