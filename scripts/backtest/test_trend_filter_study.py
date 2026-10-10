"""Unit tests for issue #9's `trend_filter_study.py`: cohort classification
off a stored `model_input`, exit-profile/period boundaries, the isolated
trade simulation, and the non-overlapping de-duplication the conclusion
rests on."""

import _bootstrap  # noqa: F401,I001 -- must patch sys.path before the imports below
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from common.config import RiskConfig  # noqa: E402
from common.enums import PREFILTER_MODEL_NAME  # noqa: E402
from ledger import MINIMAL_ROI, Ledger  # noqa: E402
from trend_filter_study import (  # noqa: E402
    BLOCKED,
    ELIGIBLE,
    IN_POSITION,
    INSUFFICIENT,
    Outcome,
    classify,
    exit_profile_at,
    non_overlapping,
    parse_signal,
    period_of,
    r_stats,
    simulate_trade,
    welch_t,
)

TS = datetime(2026, 9, 20, 10, tzinfo=timezone.utc)
HOUR_MS = 3_600_000


def _model_input(*, ema_50, ema_200, price=110.0, macd_hist=0.5, rsi=60.0, open_position=False):
    candles = [
        {"t": f"2026-09-20T0{h}:00:00+00:00", "o": price, "h": price + 1, "l": price - 1,
         "c": price, "v": 10.0}
        for h in range(6, 10)
    ]
    return {
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candle_close_time": TS.isoformat(),
        "ohlcv": candles,
        "indicators": {
            "rsi_14": rsi, "ema_50": ema_50, "ema_200": ema_200,
            "macd": {"macd": 1.0, "signal": 1.0 - macd_hist, "histogram": macd_hist},
            "atr_14": 1.0, "volume_sma_20": 100.0,
        },
        "position_context": {"has_open_position": open_position, "unrealized_pnl_pct": None},
    }


def _row(model_input, *, model_name="claude-sonnet", raw_response=None, reasoning="x"):
    return {
        "id": "sig-1", "symbol": "BTC/USDT", "timeframe": "1h",
        "candle_ts": TS.isoformat(), "action": "HOLD", "reasoning": reasoning,
        "model_name": model_name, "raw_response": raw_response,
        "model_input": model_input, "price": 110.0, "atr_14": 1.0,
    }


def test_uptrend_trend_following_with_sufficient_entry_is_blocked():
    # EMA gap 5% (> 1.5% threshold), price above both, MACD bullish, RSI 60.
    signal = parse_signal(_row(_model_input(ema_50=105.0, ema_200=100.0)))
    c = classify(signal)
    assert c.cohort == BLOCKED
    assert c.regime == "trend_following"
    assert c.source == "llm_failed"  # no raw_response => no model output


def test_flat_trend_with_sufficient_entry_is_eligible():
    # EMA gap 0.5%: inside the trend threshold, so not TREND_FOLLOWING.
    signal = parse_signal(_row(_model_input(ema_50=100.5, ema_200=100.0)))
    assert classify(signal).cohort == ELIGIBLE


def test_failed_entry_bar_and_open_position_are_not_simulated_cohorts():
    no_momentum = _model_input(ema_50=105.0, ema_200=100.0, macd_hist=-0.5, rsi=80.0)
    assert classify(parse_signal(_row(no_momentum))).cohort == INSUFFICIENT
    in_position = _model_input(ema_50=105.0, ema_200=100.0, open_position=True)
    assert classify(parse_signal(_row(in_position))).cohort == IN_POSITION


def test_model_output_comes_from_raw_text_not_the_capped_column():
    raw = {"raw": json.dumps({"action": "BUY", "confidence": 0.74}), "model_action": "BUY"}
    signal = parse_signal(_row(_model_input(ema_50=105.0, ema_200=100.0), raw_response=raw))
    assert signal.model_action == "BUY"
    assert signal.model_confidence == 0.74
    assert classify(signal).source == "llm"


def test_prefilter_source_and_stored_reason_flag():
    reasoning = "Pre-filter HOLD ... the setup is trend_following, where BUY is always suppressed."
    signal = parse_signal(_row(
        _model_input(ema_50=105.0, ema_200=100.0),
        model_name=PREFILTER_MODEL_NAME, reasoning=reasoning,
    ))
    c = classify(signal)
    assert c.source == "prefilter"
    assert c.stored_says_trend_following is True


def test_rows_without_model_input_are_skipped():
    assert parse_signal(_row(None)) is None


def test_exit_profile_follows_deploy_boundaries():
    assert exit_profile_at(datetime(2026, 8, 20, tzinfo=timezone.utc))[0] == "pre_pr18"
    label, profile = exit_profile_at(datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert label == "pr18_x3" and profile.minimal_roi[0] == Decimal("0.18")
    assert exit_profile_at(datetime(2026, 9, 6, tzinfo=timezone.utc))[1].minimal_roi[0] == (
        Decimal("0.10")
    )
    label, profile = exit_profile_at(datetime(2026, 9, 9, tzinfo=timezone.utc))
    assert label == "reverted" and profile.minimal_roi == MINIMAL_ROI


def test_period_boundaries():
    assert period_of(datetime(2026, 9, 6, tzinfo=timezone.utc)) == "qwen"
    assert period_of(datetime(2026, 9, 8, tzinfo=timezone.utc)) == "outage"
    assert period_of(datetime(2026, 9, 9, tzinfo=timezone.utc)) == "sonnet"


def test_ledger_default_exit_profile_is_unchanged():
    assert Ledger(starting_equity_usdt=Decimal("1")).exit_profile.minimal_roi == MINIMAL_ROI


def _candles(prices_after_entry):
    """Fill candle opens at TS (the signal's decision-candle close)."""
    start = int(TS.timestamp() * 1000)
    candles = [{"t": start - HOUR_MS, "o": 110, "h": 111, "l": 109, "c": 110, "v": 10}]
    for k, bar in enumerate(prices_after_entry):
        o, h, low, c = bar if len(bar) == 4 else (110, *bar)
        candles.append({"t": start + k * HOUR_MS, "o": o, "h": h, "l": low, "c": c, "v": 10})
    return candles


def _simulate(candles, *, decider=lambda *_: False, max_hold_hours=336):
    signal = parse_signal(_row(_model_input(ema_50=105.0, ema_200=100.0)))
    return simulate_trade(
        signal, classify(signal), candles, timeframe="1h", risk_config=RiskConfig(),
        exit_decider=decider, max_hold_hours=max_hold_hours, fee_pct=Decimal("0.001"),
    )


def test_simulated_trade_takes_minimal_roi():
    # Second candle gaps up: peak +6.2% clears the 6% ROI tier while its low
    # stays above the trailing stop (peak x 0.985), so ROI fires, not trail.
    outcome = _simulate(_candles([(110.5, 109.5, 110.0), (116.0, 116.8, 115.9, 116.5)]))
    assert outcome.status == "closed"
    assert outcome.exit_reason == "minimal_roi"
    assert outcome.r_multiple > 0
    assert outcome.exit_profile == "reverted"


def test_simulated_trade_hits_atr_stop():
    # ATR 1.0 x multiplier 2 => stop at 108; the first candle's low pierces it.
    outcome = _simulate(_candles([(110.5, 100.0, 101.0), (102, 100, 101)]))
    assert outcome.exit_reason == "atr_stoploss"
    assert outcome.r_multiple < 0


def test_simulated_trade_exits_on_rubric_sell_at_next_open():
    outcome = _simulate(
        _candles([(111, 109, 110.5), (111, 109, 110.5), (111, 109, 110.5)]),
        decider=lambda symbol, i, entry: i == 1,
    )
    assert outcome.exit_reason == "llm_sell_signal"


def test_simulated_trade_marks_to_market_at_horizon():
    flat = [(111, 109, 110.5)] * 5
    outcome = _simulate(_candles(flat), max_hold_hours=2)
    assert outcome.status == "horizon"
    assert outcome.r_multiple is not None


def test_missing_fill_candle_reports_no_candles():
    assert _simulate([]).status == "no_candles"


def _outcome(cohort, symbol, start_h, exit_h, r):
    signal = parse_signal(_row(_model_input(ema_50=105.0, ema_200=100.0)))
    signal = replace(signal, symbol=symbol, candle_ts=TS + timedelta(hours=start_h))
    o = Outcome(signal, replace(classify(signal), cohort=cohort), "reverted", "closed")
    o.r_multiple = Decimal(str(r))
    o.exit_time = TS + timedelta(hours=exit_h)
    return o


def test_non_overlapping_keeps_one_open_trade_per_symbol_and_cohort():
    outcomes = [
        _outcome(BLOCKED, "BTC/USDT", 0, 5, 1),
        _outcome(BLOCKED, "BTC/USDT", 2, 6, 1),  # overlaps the first -> dropped
        _outcome(BLOCKED, "BTC/USDT", 5, 8, -1),  # starts at the exit -> kept
        _outcome(BLOCKED, "ETH/USDT", 2, 3, 1),  # other symbol -> kept
        _outcome(ELIGIBLE, "BTC/USDT", 1, 2, 1),  # other cohort -> kept
    ]
    kept = non_overlapping(outcomes)
    assert [(o.classification.cohort, o.signal.symbol, o.signal.candle_ts.hour) for o in kept] == [
        (BLOCKED, "BTC/USDT", 10),
        (ELIGIBLE, "BTC/USDT", 11),
        (BLOCKED, "ETH/USDT", 12),
        (BLOCKED, "BTC/USDT", 15),
    ]


def test_r_stats_and_welch():
    stats = r_stats([1.0, -1.0, 2.0, -0.5])
    assert stats.n == 4 and stats.win_rate == 0.5
    assert stats.mean_r == 0.375 and stats.total_r == 1.5
    assert stats.ci95[0] < stats.mean_r < stats.ci95[1]
    assert r_stats([]).mean_r is None
    assert welch_t([1.0], [2.0, 3.0]) is None
    assert welch_t([1.0, 1.2, 0.8], [-1.0, -1.2, -0.8]) > 2
