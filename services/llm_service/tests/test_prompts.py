import json

from llm_service.app.prompts.v1 import build_user_prompt
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
