import json

from common.enums import Action, SignalStatus

from llm_service.app.context.builder import ContextBuilder
from llm_service.app.models.strategy import SelectedStrategy, StrategyName
from llm_service.app.models.volatility import VolatilityRegime
from llm_service.app.models.wire import AnalyzeRequest
from llm_service.app.scoring.trade_score import TradeScorer
from llm_service.app.signals.generator import SignalGenerator
from llm_service.app.validators.structural import parse_llm_response

VALID_RESPONSE = json.dumps(
    {
        "action": "SELL",
        "confidence": 0.9,
        "reasoning": "Strong bearish divergence on the 1h chart.",
        "key_indicators": ["macd_cross"],
        "invalidation_condition": "Close above EMA200.",
    }
)


def _context():
    request = AnalyzeRequest(
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
    return ContextBuilder().build(request)


def _strategy() -> SelectedStrategy:
    return SelectedStrategy(
        strategy=StrategyName.MOMENTUM_CONTINUATION,
        possible_alternatives=(),
        reasoning="test reasoning",
    )


def test_build_hold_produces_pending_hold_signal():
    context = _context()

    signal = SignalGenerator().build_hold(
        context, reason="llm_timeout", model_name="anthropic:claude-sonnet-5"
    )

    assert signal.action == Action.HOLD
    assert signal.confidence == 0.0
    assert signal.reasoning == "llm_timeout"
    assert signal.status == SignalStatus.PENDING
    assert signal.symbol == "BTC/USDT"


def test_build_hold_leaves_a_none_raw_response_untouched_even_with_a_strategy():
    """A total provider failure keeps `raw_response=None` exactly as before
    this refactor — enrichment only ever adds to an existing dict, it never
    turns a null into one (see SignalGenerator's module docstring)."""
    context = _context()

    signal = SignalGenerator().build_hold(
        context, reason="llm_timeout", model_name="anthropic:claude-sonnet-5", strategy=_strategy()
    )

    assert signal.raw_response is None


def test_build_hold_enriches_an_existing_raw_response_dict_with_strategy_metadata():
    context = _context()
    strategy = _strategy()

    signal = SignalGenerator().build_hold(
        context,
        reason="malformed_json",
        model_name="anthropic:claude-sonnet-5",
        strategy=strategy,
        raw_response={"raw": "not json"},
    )

    assert signal.raw_response["raw"] == "not json"
    assert signal.raw_response["strategy_selected"] == "momentum_continuation"
    assert signal.raw_response["strategy_reasoning"] == "test reasoning"
    assert "generated_at" in signal.raw_response


def test_build_signal_carries_through_llm_output():
    context = _context()
    output = parse_llm_response(VALID_RESPONSE)

    signal = SignalGenerator().build_signal(
        context,
        output,
        model_name="anthropic:claude-sonnet-5",
        raw_response={"raw": VALID_RESPONSE},
    )

    assert signal.action == Action.SELL
    assert signal.confidence == 0.9
    assert signal.reasoning == output.reasoning
    assert signal.raw_response["raw"] == VALID_RESPONSE


def test_build_signal_attaches_strategy_metadata_without_dropping_existing_keys():
    context = _context()
    output = parse_llm_response(VALID_RESPONSE)
    strategy = _strategy()

    signal = SignalGenerator().build_signal(
        context,
        output,
        model_name="anthropic:claude-sonnet-5",
        strategy=strategy,
        raw_response={"raw": VALID_RESPONSE, "model_action": "SELL"},
    )

    assert signal.raw_response["raw"] == VALID_RESPONSE
    assert signal.raw_response["model_action"] == "SELL"
    assert signal.raw_response["strategy_selected"] == "momentum_continuation"


def test_build_signal_attaches_journal_fields_as_first_class_columns_not_raw_response():
    """Positive-expectancy plan D3: trade_score/score_breakdown/setup_regime/
    volatility_regime are top-level `TradingSignal` fields, not buried in
    `raw_response` — unlike `strategy_selected` (D2/Section 1's gap)."""
    context = _context()
    output = parse_llm_response(VALID_RESPONSE)
    strategy = _strategy()
    score = TradeScorer().score(context, strategy, VolatilityRegime.NORMAL)

    signal = SignalGenerator().build_signal(
        context,
        output,
        model_name="anthropic:claude-sonnet-5",
        strategy=strategy,
        volatility_regime=VolatilityRegime.NORMAL,
        score=score,
    )

    assert signal.trade_score == score.total
    assert signal.score_breakdown == score.breakdown()
    assert signal.setup_regime == "momentum_continuation"
    assert signal.volatility_regime == "NORMAL"


def test_build_hold_attaches_journal_fields_even_on_a_failure_path():
    """Strategy/volatility/score are computed before the LLM call
    (`AnalysisPipeline.run`), so even a total provider failure's HOLD signal
    carries them — the whole point of computing them once per cycle."""
    context = _context()
    strategy = _strategy()
    score = TradeScorer().score(context, strategy, VolatilityRegime.HIGH_VOLATILITY)

    signal = SignalGenerator().build_hold(
        context,
        reason="llm_timeout",
        model_name="anthropic:claude-sonnet-5",
        strategy=strategy,
        volatility_regime=VolatilityRegime.HIGH_VOLATILITY,
        score=score,
    )

    assert signal.trade_score == score.total
    assert signal.setup_regime == "momentum_continuation"
    assert signal.volatility_regime == "HIGH_VOLATILITY"


def test_journal_fields_default_to_none_when_not_supplied():
    context = _context()

    signal = SignalGenerator().build_hold(
        context, reason="llm_timeout", model_name="anthropic:claude-sonnet-5"
    )

    assert signal.trade_score is None
    assert signal.score_breakdown is None
    assert signal.setup_regime is None
    assert signal.volatility_regime is None
