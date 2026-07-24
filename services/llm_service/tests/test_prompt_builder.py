import json
from pathlib import Path

from llm_service.app.context.builder import ContextBuilder
from llm_service.app.models.market import MarketContext
from llm_service.app.models.strategy import SelectedStrategy, StrategyName
from llm_service.app.models.wire import AnalyzeRequest, ProviderOverride
from llm_service.app.prompts.builder import PromptBuilder
from llm_service.app.prompts.v1 import SYSTEM_PROMPT_V1, build_user_prompt

FIXTURES = Path(__file__).parent / "fixtures"


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


def _context(**overrides) -> MarketContext:
    return ContextBuilder().build(_base_request(**overrides))


def _strategy() -> SelectedStrategy:
    return SelectedStrategy(
        strategy=StrategyName.MEAN_REVERSION,
        possible_alternatives=(),
        reasoning="EMA50/EMA200 gap is inside the trend threshold.",
    )


# --- build_user_prompt (unchanged Section 8.1 wire contract) ---------------


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


def test_bearish_open_position_fixture_reaches_model_with_exit_context():
    """Regression fixture based on the VPS BTC case that was repeatedly
    classified HOLD despite an open position and aligned bearish evidence."""
    request = _base_request(
        ohlcv=[
            {"t": "2026-07-17T01:40:00Z", "o": 64010, "h": 64020, "l": 63940, "c": 63955, "v": 45},
            {"t": "2026-07-17T01:45:00Z", "o": 63955, "h": 63970, "l": 63820, "c": 63840, "v": 70},
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


def test_live_regression_fixtures_encode_unambiguous_rubric_cases():
    bearish = AnalyzeRequest.model_validate_json(
        (FIXTURES / "regression_bearish_open.json").read_text()
    )
    bullish = AnalyzeRequest.model_validate_json(
        (FIXTURES / "regression_bullish_flat.json").read_text()
    )

    bearish_close = bearish.ohlcv[-1].c
    assert bearish.position_context.has_open_position is True
    assert bearish_close < bearish.indicators.ema_50
    assert bearish_close < bearish.indicators.ema_200
    assert bearish.indicators.rsi_14 < 45
    assert bearish.indicators.macd.histogram < 0
    assert bearish.ohlcv[-1].v > bearish.indicators.volume_sma_20

    bullish_close = bullish.ohlcv[-1].c
    assert bullish.position_context.has_open_position is False
    assert bullish_close > bullish.indicators.ema_50
    assert bullish_close > bullish.indicators.ema_200
    assert 50 < bullish.indicators.rsi_14 < 70
    assert bullish.indicators.macd.histogram > 0
    assert bullish.ohlcv[-1].v > bullish.indicators.volume_sma_20


# --- SYSTEM_PROMPT_V1 (unchanged prompt text) -------------------------------


def test_system_prompt_defines_position_aware_long_only_actions():
    prompt = " ".join(SYSTEM_PROMPT_V1.split())
    assert "Never return BUY when has_open_position is true" in prompt
    assert "Never return SELL when has_open_position is false" in prompt
    assert "bearish data without a position is HOLD" in prompt


def test_system_prompt_requires_multiple_independent_confirmations():
    prompt = " ".join(SYSTEM_PROMPT_V1.split())
    assert "at least three independent bullish confirmations" in prompt
    assert (
        "at least two independent bearish exit confirmations agree, spanning at least two"
        in prompt
    )
    assert "do not satisfy the rubric" in prompt
    assert "Sentiment does not count as a confirmation" in prompt
    assert "ATR is volatility rather than direction" in prompt


def test_system_prompt_has_no_phrase_rich_action_examples_to_copy():
    assert "Example BUY" not in SYSTEM_PROMPT_V1
    assert "Example SELL" not in SYSTEM_PROMPT_V1
    assert "Example HOLD" not in SYSTEM_PROMPT_V1
    assert "chopping between the 50 and 200 EMA" not in SYSTEM_PROMPT_V1


# --- PromptBuilder -----------------------------------------------------------


def test_prompt_builder_default_output_is_byte_identical_to_the_pre_refactor_prompt():
    """The default (`include_strategy_context=False`) must reproduce exactly
    what `main.py` sent to the LLM before this refactor: strategy selection
    changes signal metadata, not what the model is asked."""
    context = _context()
    request = PromptBuilder().build(context, _strategy())

    assert request.system_prompt == SYSTEM_PROMPT_V1
    assert request.user_prompt == build_user_prompt(context.request)


def test_prompt_builder_can_include_strategy_context_when_enabled():
    context = _context()
    strategy = _strategy()
    request = PromptBuilder(include_strategy_context=True).build(context, strategy)

    assert request.system_prompt.startswith(SYSTEM_PROMPT_V1)
    assert strategy.strategy.value in request.system_prompt
    assert strategy.reasoning in request.system_prompt
    assert "advisory only" in request.system_prompt
    # The user prompt (the Section 8.1 market-data JSON) is untouched either way.
    assert request.user_prompt == build_user_prompt(context.request)


def test_prompt_builder_repair_prompt_carries_the_failure_reason_and_original_response():
    context = _context()
    strategy = _strategy()
    invalid_response = "not json at all"

    repair = PromptBuilder().build_repair(context, strategy, invalid_response, "malformed_json")

    assert repair.system_prompt == SYSTEM_PROMPT_V1
    assert "malformed_json" in repair.user_prompt
    assert invalid_response in repair.user_prompt
    assert build_user_prompt(context.request) in repair.user_prompt
