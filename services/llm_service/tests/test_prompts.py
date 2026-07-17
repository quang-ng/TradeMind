import json

from llm_service.app.prompts.v1 import SYSTEM_PROMPT_V1, build_user_prompt
from llm_service.app.schemas import AnalyzeRequest, ProviderOverride


def _base_request(**overrides) -> AnalyzeRequest:
    payload = {
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candle_close_time": "2026-07-15T13:00:00Z",
        "ohlcv": [
            {"t": "2026-07-15T12:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
        ],
        "indicators": {
            "rsi_14": 44.2,
            "ema_50": 61980.1,
            "ema_200": 59340.7,
            "macd": {"macd": 120.4, "signal": 98.1, "histogram": 22.3},
            "atr_14": 780.5,
            "volume_sma_20": 690.2,
        },
        "position_context": {"has_open_position": False, "unrealized_pnl_pct": None},
        **overrides,
    }
    return AnalyzeRequest.model_validate(payload)


def test_build_user_prompt_excludes_provider_override_from_what_the_llm_sees():
    """PROJECT.md Section 8.1 defines exactly what the LLM receives;
    `provider_override` is internal request-routing metadata (Section 3/8.4),
    not market data, and must never reach the model."""
    request = _base_request(
        provider_override=ProviderOverride(llm_provider="ollama", ollama_temperature=0.7)
    )

    prompt = build_user_prompt(request)

    assert "provider_override" not in prompt
    assert "ollama" not in prompt
    parsed = json.loads(prompt)
    assert set(parsed.keys()) == {
        "symbol",
        "timeframe",
        "candle_close_time",
        "ohlcv",
        "indicators",
        "position_context",
    }


def test_build_user_prompt_unaffected_when_no_override_present():
    request = _base_request()
    prompt = build_user_prompt(request)
    assert json.loads(prompt)["symbol"] == "BTC/USDT"


def test_build_user_prompt_includes_advisory_sentiment_when_present():
    request = _base_request(
        sentiment={
            "score": 25,
            "state": "FEAR",
            "confidence": 0.85,
            "reasons": ["RSI(14) is oversold at 25.0"],
        }
    )

    parsed = json.loads(build_user_prompt(request))

    assert parsed["sentiment"] == {
        "score": 25,
        "state": "FEAR",
        "confidence": 0.85,
        "reasons": ["RSI(14) is oversold at 25.0"],
    }


def test_system_prompt_defines_position_aware_long_only_actions():
    prompt = " ".join(SYSTEM_PROMPT_V1.split())
    assert "Never return BUY when has_open_position is true" in prompt
    assert "Never return SELL when has_open_position is false" in prompt
    assert "bearish data without a position is HOLD" in prompt


def test_system_prompt_requires_multiple_independent_confirmations():
    prompt = " ".join(SYSTEM_PROMPT_V1.split())
    assert "at least three independent bullish confirmations" in prompt
    assert "at least three independent bearish exit confirmations" in prompt
    assert "Sentiment does not count as a confirmation" in prompt
    assert "ATR is volatility rather than direction" in prompt


def test_system_prompt_has_no_phrase_rich_action_examples_to_copy():
    assert "Example BUY" not in SYSTEM_PROMPT_V1
    assert "Example SELL" not in SYSTEM_PROMPT_V1
    assert "Example HOLD" not in SYSTEM_PROMPT_V1
    assert "chopping between the 50 and 200 EMA" not in SYSTEM_PROMPT_V1


def test_bearish_open_position_fixture_reaches_model_with_exit_context():
    """Regression fixture based on the VPS BTC case that was repeatedly
    classified HOLD despite an open position and aligned bearish evidence."""
    request = _base_request(
        ohlcv=[
            {
                "t": "2026-07-17T01:40:00Z",
                "o": 64010,
                "h": 64020,
                "l": 63940,
                "c": 63955,
                "v": 45,
            },
            {
                "t": "2026-07-17T01:45:00Z",
                "o": 63955,
                "h": 63970,
                "l": 63820,
                "c": 63840,
                "v": 70,
            },
        ],
        indicators={
            "rsi_14": 35.48,
            "ema_50": 63923.08,
            "ema_200": 64110.51,
            "macd": {"macd": -31.23, "signal": -16.08, "histogram": -15.15},
            "atr_14": 95.29,
            "volume_sma_20": 39.36,
        },
        position_context={"has_open_position": True, "unrealized_pnl_pct": None},
    )

    parsed = json.loads(build_user_prompt(request))

    assert parsed["position_context"]["has_open_position"] is True
    assert parsed["ohlcv"][-1]["c"] < parsed["indicators"]["ema_50"]
    assert parsed["ohlcv"][-1]["c"] < parsed["indicators"]["ema_200"]
    assert parsed["indicators"]["rsi_14"] < 45
    assert parsed["indicators"]["macd"]["histogram"] < 0
