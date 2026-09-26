import random

import pytest
from common.enums import Action

from llm_service.app.context.builder import ContextBuilder
from llm_service.app.models.llm import LLMOutput
from llm_service.app.models.market import MarketContext
from llm_service.app.models.strategy import StrategyName
from llm_service.app.models.wire import AnalyzeRequest
from llm_service.app.strategies.selector import StrategySelector
from llm_service.app.validators.prefilter import LLMCallPrefilter
from llm_service.app.validators.semantic import validate_signal_semantics

THRESHOLDS = {"min_exit_profit_pct": 0.005, "min_exit_loss_pct": 0.005, "hard_loss_cut_pct": 0.015}


def _output(action: Action) -> LLMOutput:
    return LLMOutput(
        action=action,
        confidence=0.78,
        reasoning="Model reasoning.",
        key_indicators=[],
        invalidation_condition="Model invalidation.",
    )


def _random_context(rng: random.Random) -> MarketContext:
    """A random but schema-valid market snapshot. Ranges are chosen so every
    entry/exit confirmation flips both ways across the sample."""
    price = 100.0
    candles = []
    for i in range(4):
        o = price
        c = price * (1 + rng.uniform(-0.02, 0.02))
        candles.append({
            "t": str(i), "o": o, "h": max(o, c) * 1.005, "l": min(o, c) * 0.995,
            "c": c, "v": rng.uniform(50, 150),
        })
        price = c
    macd = rng.uniform(-2, 2)
    signal = rng.uniform(-2, 2)
    has_open_position = rng.random() < 0.5
    pnl = None
    if has_open_position and rng.random() < 0.9:
        pnl = rng.uniform(-0.03, 0.03)
    request = AnalyzeRequest(
        symbol="BTC/USDT",
        timeframe="1h",
        candle_close_time="2026-09-26T03:00:00Z",
        ohlcv=candles,
        indicators={
            "rsi_14": rng.uniform(20, 80),
            "ema_50": price * rng.uniform(0.97, 1.03),
            "ema_200": price * rng.uniform(0.95, 1.05),
            "macd": {"macd": macd, "signal": signal, "histogram": macd - signal},
            "atr_14": rng.uniform(0.5, 3.0),
            "volume_sma_20": 100.0,
        },
        position_context={"has_open_position": has_open_position, "unrealized_pnl_pct": pnl},
    )
    return ContextBuilder().build(request)


def _contexts(n: int = 3000) -> list[MarketContext]:
    rng = random.Random(20260926)
    return [_random_context(rng) for _ in range(n)]


def test_skipped_contexts_resolve_to_hold_for_every_possible_model_answer():
    """The pre-filter's correctness contract: whenever it skips the LLM,
    `validate_signal_semantics` would have forced HOLD no matter what the
    model said — so skipping can never change a decision."""
    prefilter = LLMCallPrefilter(**THRESHOLDS)
    skipped = 0
    for context in _contexts():
        if not prefilter.should_skip_llm(context):
            continue
        skipped += 1
        for action in Action:
            result = validate_signal_semantics(context, _output(action), **THRESHOLDS)
            assert result.output.action == Action.HOLD, (action, context)
    assert skipped > 1000  # the sample must actually exercise the skip path


def test_non_skipped_contexts_can_produce_a_trade():
    """The converse: the pre-filter never over-reaches — every context it
    sends to the LLM has at least one model answer that survives as a trade."""
    prefilter = LLMCallPrefilter(**THRESHOLDS)
    kept = 0
    for context in _contexts():
        if prefilter.should_skip_llm(context):
            continue
        kept += 1
        outcomes = {
            validate_signal_semantics(context, _output(action), **THRESHOLDS).output.action
            for action in Action
        }
        assert outcomes - {Action.HOLD}, context
    assert kept > 50


def _flat_context(**indicator_overrides) -> MarketContext:
    indicators = {
        "rsi_14": 58.0,
        "ema_50": 100.0,
        "ema_200": 99.5,
        "macd": {"macd": 1.0, "signal": 0.5, "histogram": 0.5},
        "atr_14": 2.0,
        "volume_sma_20": 100.0,
    }
    indicators.update(indicator_overrides)
    return ContextBuilder().build(AnalyzeRequest(
        symbol="ETH/USDT",
        timeframe="1h",
        candle_close_time="2026-09-26T03:00:00Z",
        ohlcv=[
            {"t": "1", "o": 100, "h": 101, "l": 99.5, "c": 100.5, "v": 90},
            {"t": "2", "o": 100.5, "h": 101.5, "l": 100, "c": 101, "v": 95},
            {"t": "3", "o": 101, "h": 102, "l": 100.5, "c": 101.5, "v": 120},
        ],
        indicators=indicators,
        position_context={"has_open_position": False},
    ))


def test_calls_llm_for_a_fresh_qualifying_entry():
    context = _flat_context()
    assert StrategySelector().select(context).strategy != StrategyName.TREND_FOLLOWING
    assert not LLMCallPrefilter().should_skip_llm(context)


def test_skips_llm_when_entry_confirmations_are_insufficient():
    context = _flat_context(
        rsi_14=40.0, macd={"macd": -1.0, "signal": 0.5, "histogram": -1.5}
    )
    prefilter = LLMCallPrefilter()
    assert prefilter.should_skip_llm(context)
    reason = prefilter.hold_reason(context)
    assert reason.startswith("Pre-filter HOLD without an LLM call: no open position")
    assert "at least three" in reason


def test_skips_llm_for_trend_following_setup_even_with_full_confirmations():
    # EMA50 3% above EMA200 with price above both: past the selector's 1.5%
    # trend threshold, so semantic.py would suppress any BUY.
    context = _flat_context(ema_50=100.0, ema_200=97.0)
    assert StrategySelector().select(context).strategy == StrategyName.TREND_FOLLOWING
    prefilter = LLMCallPrefilter()
    assert prefilter.should_skip_llm(context)
    assert "trend_following" in prefilter.hold_reason(context)


def _open_context(pnl: float | None) -> MarketContext:
    return ContextBuilder().build(AnalyzeRequest(
        symbol="ETH/USDT",
        timeframe="1h",
        candle_close_time="2026-09-26T03:00:00Z",
        ohlcv=[
            {"t": "1", "o": 103, "h": 104, "l": 101, "c": 102, "v": 90},
            {"t": "2", "o": 102, "h": 103, "l": 100, "c": 101, "v": 95},
            {"t": "3", "o": 101, "h": 102, "l": 99, "c": 99.5, "v": 150},
        ],
        indicators={
            "rsi_14": 38.0,
            "ema_50": 102.0,
            "ema_200": 103.0,
            "macd": {"macd": -1.0, "signal": 0.2, "histogram": -1.2},
            "atr_14": 2.0,
            "volume_sma_20": 100.0,
        },
        position_context={"has_open_position": True, "unrealized_pnl_pct": pnl},
    ))


@pytest.mark.parametrize("pnl", [0.02, -0.01])
def test_calls_llm_for_confirmed_exit_beyond_the_cushion(pnl):
    assert not LLMCallPrefilter().should_skip_llm(_open_context(pnl))


@pytest.mark.parametrize("pnl", [0.0, 0.004, -0.004, None])
def test_skips_llm_when_pnl_is_inside_the_cushion_or_unknown(pnl):
    prefilter = LLMCallPrefilter()
    context = _open_context(pnl)
    assert prefilter.should_skip_llm(context)
    assert "open position" in prefilter.hold_reason(context)


def test_keeps_calling_llm_at_the_hard_loss_cut():
    """The backstop forces SELL only once a valid model response exists;
    the pre-filter must not change that behavior as a side effect."""
    assert not LLMCallPrefilter().should_skip_llm(_open_context(-0.02))


def test_hold_reason_fits_the_signal_reasoning_limit():
    prefilter = LLMCallPrefilter()
    for context in _contexts(500):
        if prefilter.should_skip_llm(context):
            assert 0 < len(prefilter.hold_reason(context)) <= 500
