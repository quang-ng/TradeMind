import json

from common.enums import Action
from llm_service.app.context.builder import ContextBuilder
from llm_service.app.models.wire import AnalyzeRequest
from llm_service.app.validators.response_validator import ResponseValidator

VALID_BUY_RESPONSE = json.dumps(
    {
        "action": "BUY",
        "confidence": 0.78,
        "reasoning": "Three aligned bullish confirmations.",
        "key_indicators": ["ema50_reclaim"],
        "invalidation_condition": "Close back below EMA50.",
    }
)


def _no_position_context():
    request = AnalyzeRequest(
        symbol="BTC/USDT",
        timeframe="1h",
        candle_close_time="2026-07-15T13:00:00Z",
        ohlcv=[{"t": "2026-07-15T12:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}],
        indicators={
            "rsi_14": 55.0,
            "ema_50": 100.0,
            "ema_200": 90.0,
            "macd": {"macd": 1.0, "signal": 0.5, "histogram": 0.5},
            "atr_14": 2.0,
            "volume_sma_20": 10.0,
        },
        position_context={"has_open_position": False, "unrealized_pnl_pct": None},
    )
    return ContextBuilder().build(request)


async def _unreachable_repair(bad_text: str, reason: str) -> str | None:
    raise AssertionError("repair must not be called when max_repair_attempts is 0")


async def test_valid_response_is_accepted_and_original_action_matches_the_model():
    validator = ResponseValidator()
    result = await validator.validate(VALID_BUY_RESPONSE, _no_position_context())

    assert result.is_valid is True
    assert result.output.action == Action.BUY
    assert result.original_action == Action.BUY
    assert result.action_changed is False
    assert result.repair_attempted is False


async def test_malformed_response_fails_closed_to_invalid_without_calling_repair_by_default():
    """PROJECT.md Section 8.3's documented behavior: no retry for a
    malformed/schema-invalid response. `max_repair_attempts` defaults to 0,
    so `repair` is never invoked even when one is supplied."""
    validator = ResponseValidator()
    result = await validator.validate(
        "not json at all", _no_position_context(), repair=_unreachable_repair
    )

    assert result.is_valid is False
    assert result.failure_reason == "malformed_json"
    assert result.repair_attempted is False


async def test_repair_prompt_retry_recovers_when_enabled():
    calls: list[tuple[str, str]] = []

    async def repair(bad_text: str, reason: str) -> str | None:
        calls.append((bad_text, reason))
        return VALID_BUY_RESPONSE

    validator = ResponseValidator(max_repair_attempts=1)
    result = await validator.validate("not json at all", _no_position_context(), repair=repair)

    assert calls == [("not json at all", "malformed_json")]
    assert result.is_valid is True
    assert result.output.action == Action.BUY
    assert result.repair_attempted is True
    assert result.final_raw_text == VALID_BUY_RESPONSE


async def test_repair_prompt_retry_gives_up_after_exhausting_attempts():
    async def repair(bad_text: str, reason: str) -> str | None:
        return "still not json"

    validator = ResponseValidator(max_repair_attempts=1)
    result = await validator.validate("not json at all", _no_position_context(), repair=repair)

    assert result.is_valid is False
    assert result.failure_reason == "malformed_json"
    assert result.repair_attempted is True
    assert result.final_raw_text == "still not json"


async def test_repair_callback_returning_none_stops_without_looping_forever():
    calls = 0

    async def failing_repair(bad_text: str, reason: str) -> str | None:
        nonlocal calls
        calls += 1
        return None

    validator = ResponseValidator(max_repair_attempts=3)
    result = await validator.validate(
        "not json at all", _no_position_context(), repair=failing_repair
    )

    assert calls == 1
    assert result.is_valid is False
    assert result.repair_attempted is True


async def test_never_fabricates_buy_or_sell_on_a_permanently_invalid_response():
    validator = ResponseValidator(max_repair_attempts=2)

    async def repair(bad_text: str, reason: str) -> str | None:
        return "garbage"

    result = await validator.validate("garbage", _no_position_context(), repair=repair)

    assert result.is_valid is False
    assert result.output is None
