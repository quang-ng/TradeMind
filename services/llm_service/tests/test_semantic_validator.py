import json
from pathlib import Path

from common.enums import Action
from llm_service.app.schemas import AnalyzeRequest, LLMOutput
from llm_service.app.semantic_validator import validate_signal_semantics

FIXTURES = Path(__file__).parent / "fixtures"


def _output(action: Action) -> LLMOutput:
    return LLMOutput(
        action=action,
        confidence=0.55,
        reasoning="Model reasoning.",
        key_indicators=[],
        invalidation_condition="Model invalidation.",
    )


def _request(*, has_open_position: bool, bearish: bool) -> AnalyzeRequest:
    if bearish:
        payload = json.loads((FIXTURES / "regression_bearish_open.json").read_text())
        payload["position_context"]["has_open_position"] = has_open_position
        return AnalyzeRequest.model_validate(payload)

    return AnalyzeRequest(
        symbol="ETH/USDT",
        timeframe="5m",
        candle_close_time="2026-07-17T03:35:00Z",
        ohlcv=[
            {"t": "1", "o": 100, "h": 102, "l": 99, "c": 101, "v": 80},
            {"t": "2", "o": 101, "h": 103, "l": 100, "c": 102, "v": 90},
            {"t": "3", "o": 102, "h": 104, "l": 101, "c": 103, "v": 95},
        ],
        indicators={
            "rsi_14": 56.7,
            "ema_50": 102.0,
            "ema_200": 110.0,
            "macd": {"macd": -0.3, "signal": -2.0, "histogram": 1.7},
            "atr_14": 4.0,
            "volume_sma_20": 100.0,
        },
        position_context={"has_open_position": has_open_position},
    )


def test_overrides_model_hold_when_documented_bearish_exit_rubric_passes():
    result = validate_signal_semantics(
        _request(has_open_position=True, bearish=True), _output(Action.HOLD)
    )

    assert result.output.action == Action.SELL
    assert result.action_changed is True
    assert set(result.exit_confirmations) == {
        "price_below_ema50_and_ema200",
        "bearish_macd",
        "rsi_below_45",
        "lower_highs_and_lows",
        "falling_price_on_high_volume",
    }
    assert result.output.confidence == 0.75


def test_keeps_hold_when_open_position_has_insufficient_bearish_evidence():
    result = validate_signal_semantics(
        _request(has_open_position=True, bearish=False), _output(Action.HOLD)
    )

    assert result.output.action == Action.HOLD
    assert result.action_changed is False
    assert result.exit_confirmations == ("ema50_below_ema200",)


def test_suppresses_sell_when_no_position_is_open():
    result = validate_signal_semantics(
        _request(has_open_position=False, bearish=True), _output(Action.SELL)
    )

    assert result.output.action == Action.HOLD
    assert result.action_changed is True
    assert result.exit_confirmations == ()


def test_suppresses_buy_when_position_is_already_open_and_exit_rubric_does_not_pass():
    result = validate_signal_semantics(
        _request(has_open_position=True, bearish=False), _output(Action.BUY)
    )

    assert result.output.action == Action.HOLD
    assert result.action_changed is True


def test_suppresses_model_sell_with_fewer_than_three_confirmations():
    result = validate_signal_semantics(
        _request(has_open_position=True, bearish=False), _output(Action.SELL)
    )

    assert result.output.action == Action.HOLD
    assert result.action_changed is True
    assert result.output.confidence <= 0.64
