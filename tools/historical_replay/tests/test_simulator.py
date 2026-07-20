from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from common.enums import Action

from tools.historical_replay.demo import build_demo
from tools.historical_replay.io import load_candles, load_signals, write_inputs, write_report
from tools.historical_replay.schemas import Candle, ReplayConfig, SyntheticSignal
from tools.historical_replay.simulator import ReplaySimulator

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(
    index: int,
    *,
    open_price: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100",
) -> Candle:
    open_time = START + timedelta(minutes=30 * index)
    return Candle(
        symbol="BTC/USDT",
        open_time=open_time,
        close_time=open_time + timedelta(minutes=30),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
    )


def _signal(candle: Candle, action: Action = Action.BUY) -> SyntheticSignal:
    return SyntheticSignal(
        symbol=candle.symbol,
        candle_close_time=candle.close_time,
        action=action,
        confidence=Decimal("0.80"),
        atr_14=Decimal("2"),
    )


def test_signal_executes_only_on_next_candle_open() -> None:
    first = _candle(0, low="50")
    result = ReplaySimulator().run([first], [_signal(first)])

    assert result.summary.trades == 0
    assert result.summary.open_positions == 0
    assert result.summary.pending_orders == 1

    second = _candle(1)
    result = ReplaySimulator().run([first, second], [_signal(first)])

    assert result.summary.open_positions == 1
    assert result.summary.pending_orders == 0


def test_stop_loss_wins_conservative_intrabar_tie() -> None:
    first = _candle(0)
    second = _candle(1, high="120", low="90")
    result = ReplaySimulator().run([first, second], [_signal(first)])

    assert result.summary.trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.net_pnl_usdt < 0


def test_trade_net_pnl_equals_gross_less_both_fees() -> None:
    candles, signals = build_demo()
    result = ReplaySimulator().run(candles, signals)

    for trade in result.trades:
        assert trade.net_pnl_usdt == pytest.approx(
            trade.gross_pnl_usdt - trade.entry_fee_usdt - trade.exit_fee_usdt
        )


def test_demo_exercises_roi_stop_and_risk_rejections() -> None:
    candles, signals = build_demo()
    result = ReplaySimulator().run(candles, signals)

    assert result.summary.trades == 2
    assert result.summary.wins == 1
    assert result.summary.losses == 1
    assert [trade.exit_reason for trade in result.trades] == ["roi", "stop_loss"]
    assert result.summary.open_positions == 0
    assert result.summary.pending_orders == 0
    assert result.summary.rejection_counts == {
        "MAX_POSITIONS_REACHED": 1,
        "SIGNAL_WAS_HOLD": 2,
        "NO_POSITION_TO_EXIT": 1,
        "COOLDOWN_ACTIVE": 1,
    }


def test_fees_can_turn_flat_price_into_a_loss() -> None:
    first = _candle(0)
    second = _candle(1)
    third = _candle(2)
    result = ReplaySimulator().run(
        [first, second, third],
        [_signal(first), _signal(second, Action.SELL)],
    )

    trade = result.trades[0]
    assert trade.entry_reference_price == Decimal("100")
    assert trade.exit_reference_price == Decimal("100")
    assert trade.net_pnl_usdt < 0


def test_duplicate_candles_fail_instead_of_double_processing() -> None:
    candle = _candle(0)
    with pytest.raises(ValueError, match="duplicate candle"):
        ReplaySimulator().run([candle, candle], [])


def test_jsonl_inputs_and_reports_round_trip(tmp_path) -> None:
    candles, signals = build_demo()
    input_directory = tmp_path / "inputs"
    write_inputs(input_directory, candles, signals)

    loaded_candles = load_candles(input_directory / "candles.jsonl")
    loaded_signals = load_signals(input_directory / "signals.jsonl")
    result = ReplaySimulator(ReplayConfig()).run(loaded_candles, loaded_signals)
    output_directory = tmp_path / "report"
    write_report(output_directory, result)

    assert loaded_candles == candles
    assert loaded_signals == signals
    assert (output_directory / "summary.json").is_file()
    assert (output_directory / "trades.csv").read_text().count("\n") == 3
