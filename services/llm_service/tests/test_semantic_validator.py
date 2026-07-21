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


_UNSET_PNL = object()


def _request(
    *, has_open_position: bool, bearish: bool, unrealized_pnl_pct=_UNSET_PNL
) -> AnalyzeRequest:
    if bearish:
        payload = json.loads((FIXTURES / "regression_bearish_open.json").read_text())
        payload["position_context"]["has_open_position"] = has_open_position
        if unrealized_pnl_pct is not _UNSET_PNL:
            payload["position_context"]["unrealized_pnl_pct"] = unrealized_pnl_pct
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
    assert result.output.confidence == 0.80


def test_suppresses_sell_when_confirmations_share_a_single_category():
    """Two confirmations that are both 'momentum' (RSI + MACD, which tend to
    move together) must not satisfy the rubric — only cross-category
    agreement counts as independent evidence."""
    request = AnalyzeRequest(
        symbol="ETH/USDT",
        timeframe="1h",
        candle_close_time="2026-07-17T03:35:00Z",
        ohlcv=[
            {"t": "1", "o": 100, "h": 101, "l": 99, "c": 100, "v": 50},
            {"t": "2", "o": 100, "h": 102, "l": 99, "c": 101, "v": 50},
            {"t": "3", "o": 101, "h": 103, "l": 100, "c": 102, "v": 50},
        ],
        indicators={
            "rsi_14": 40.0,
            "ema_50": 95.0,
            "ema_200": 90.0,
            "macd": {"macd": -5.0, "signal": -1.0, "histogram": -1.0},
            "atr_14": 2.0,
            "volume_sma_20": 200.0,
        },
        position_context={"has_open_position": True, "unrealized_pnl_pct": 0.01},
    )

    result = validate_signal_semantics(request, _output(Action.SELL))

    assert result.output.action == Action.HOLD
    assert result.action_changed is True
    assert result.output.confidence <= 0.64
    assert set(result.exit_confirmations) == {"bearish_macd", "rsi_below_45"}
    assert "same signal category" in result.output.reasoning


def test_allows_sell_with_two_confirmations_spanning_two_categories():
    """Momentum + price-action agreement is enough even without a third
    confirmation — the bar is category diversity, not raw count."""
    request = AnalyzeRequest(
        symbol="ETH/USDT",
        timeframe="1h",
        candle_close_time="2026-07-17T03:35:00Z",
        ohlcv=[
            {"t": "1", "o": 100, "h": 102, "l": 99, "c": 100, "v": 10},
            {"t": "2", "o": 100, "h": 112, "l": 108, "c": 110, "v": 10},
            {"t": "3", "o": 110, "h": 107, "l": 103, "c": 105, "v": 300},
        ],
        indicators={
            "rsi_14": 40.0,
            "ema_50": 95.0,
            "ema_200": 90.0,
            "macd": {"macd": 1.0, "signal": 0.5, "histogram": 2.0},
            "atr_14": 2.0,
            "volume_sma_20": 100.0,
        },
        position_context={"has_open_position": True, "unrealized_pnl_pct": 0.01},
    )

    result = validate_signal_semantics(request, _output(Action.HOLD))

    assert result.output.action == Action.SELL
    assert result.action_changed is True
    assert result.output.confidence == 0.65
    assert set(result.exit_confirmations) == {
        "rsi_below_45",
        "falling_price_on_high_volume",
    }


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


def test_keeps_hold_when_bearish_rubric_passes_but_position_is_not_profitable():
    result = validate_signal_semantics(
        _request(has_open_position=True, bearish=True, unrealized_pnl_pct=-0.025),
        _output(Action.HOLD),
    )

    assert result.output.action == Action.HOLD
    assert result.action_changed is False


def test_suppresses_model_sell_when_rubric_passes_but_position_is_not_profitable():
    """The rubric only ever locks in gains — it must never force an exit
    that realizes a loss, even when the model itself proposes SELL and every
    bearish confirmation agrees."""
    result = validate_signal_semantics(
        _request(has_open_position=True, bearish=True, unrealized_pnl_pct=-0.025),
        _output(Action.SELL),
    )

    assert result.output.action == Action.HOLD
    assert result.action_changed is True
    assert result.output.confidence <= 0.64


def test_suppresses_model_sell_when_rubric_passes_but_pnl_is_unknown():
    result = validate_signal_semantics(
        _request(has_open_position=True, bearish=True, unrealized_pnl_pct=None),
        _output(Action.SELL),
    )

    assert result.output.action == Action.HOLD
    assert result.action_changed is True


def test_suppresses_model_sell_when_profit_does_not_clear_minimum_margin():
    """Barely positive isn't enough — analyze latency + forceexit slippage
    can flip a razor-thin gain into a net loss after round-trip fees."""
    result = validate_signal_semantics(
        _request(has_open_position=True, bearish=True, unrealized_pnl_pct=0.002),
        _output(Action.SELL),
        min_exit_profit_pct=0.005,
    )

    assert result.output.action == Action.HOLD
    assert result.action_changed is True


def test_allows_sell_once_profit_clears_the_configured_margin():
    result = validate_signal_semantics(
        _request(has_open_position=True, bearish=True, unrealized_pnl_pct=0.01),
        _output(Action.HOLD),
        min_exit_profit_pct=0.005,
    )

    assert result.output.action == Action.SELL
    assert result.action_changed is True
