import json

import pytest

from common.enums import Action, SignalStatus
from llm_service.app.schemas import AnalyzeRequest
from llm_service.app.validators import (
    ValidationFailure,
    build_hold_signal,
    build_signal,
    parse_llm_response,
)

VALID_RESPONSE = json.dumps(
    {
        "action": "SELL",
        "confidence": 0.9,
        "reasoning": "Strong bearish divergence on the 1h chart.",
        "key_indicators": ["macd_cross"],
        "invalidation_condition": "Close above EMA200.",
    }
)


def _sample_request() -> AnalyzeRequest:
    return AnalyzeRequest(
        symbol="BTC/USDT",
        timeframe="1h",
        candle_close_time="2026-07-15T13:00:00Z",
        ohlcv=[{"t": "2026-07-15T12:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}],
        indicators={
            "rsi_14": 50.0,
            "ema_50": 100.0,
            "ema_200": 90.0,
            "macd": {"macd": 1.0, "signal": 0.5, "histogram": 0.5},
            "atr_14": 2.0,
            "volume_sma_20": 10.0,
        },
        position_context={"has_open_position": False, "unrealized_pnl_pct": None},
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
        (json.dumps({"confidence": 0.5, "reasoning": "x", "key_indicators": [], "invalidation_condition": "x"}), "schema_invalid"),
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


def test_build_hold_signal_produces_pending_hold_signal():
    request = _sample_request()

    signal = build_hold_signal(request, reason="llm_timeout", model_name="anthropic:claude-sonnet-5")

    assert signal.action == Action.HOLD
    assert signal.confidence == 0.0
    assert signal.reasoning == "llm_timeout"
    assert signal.status == SignalStatus.PENDING
    assert signal.symbol == "BTC/USDT"


def test_build_signal_carries_through_llm_output():
    request = _sample_request()
    output = parse_llm_response(VALID_RESPONSE)

    signal = build_signal(request, output, model_name="anthropic:claude-sonnet-5", raw_response={"raw": VALID_RESPONSE})

    assert signal.action == Action.SELL
    assert signal.confidence == 0.9
    assert signal.reasoning == output.reasoning
    assert signal.raw_response == {"raw": VALID_RESPONSE}
