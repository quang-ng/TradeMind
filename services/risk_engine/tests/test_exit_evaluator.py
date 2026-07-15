from datetime import timedelta

from common.config import RiskConfig
from common.enums import Action, RejectionReason
from factories import NOW, make_account, make_signal
from risk_engine.app.exit_evaluator import evaluate_exit


def test_approves_exit_when_position_open():
    result = evaluate_exit(
        signal=make_signal(action=Action.SELL, symbol="BTC/USDT"),
        account=make_account(open_position_symbols=frozenset({"BTC/USDT"})),
        config=RiskConfig(),
        now=NOW,
        is_duplicate_decision=False,
    )
    assert result.approved is True
    assert result.rejection_reason is None


def test_rejects_when_no_open_position():
    result = evaluate_exit(
        signal=make_signal(action=Action.SELL, symbol="BTC/USDT"),
        account=make_account(open_position_symbols=frozenset()),
        config=RiskConfig(),
        now=NOW,
        is_duplicate_decision=False,
    )
    assert result.approved is False
    assert result.rejection_reason == RejectionReason.NO_POSITION_TO_EXIT


def test_rejects_duplicate_exit_signal():
    result = evaluate_exit(
        signal=make_signal(action=Action.SELL, symbol="BTC/USDT"),
        account=make_account(open_position_symbols=frozenset({"BTC/USDT"})),
        config=RiskConfig(),
        now=NOW,
        is_duplicate_decision=True,
    )
    assert result.approved is False
    assert result.rejection_reason == RejectionReason.DUPLICATE_SIGNAL


def test_rejects_stale_exit_signal():
    stale_signal = make_signal(
        action=Action.SELL, symbol="BTC/USDT", candle_ts=NOW - timedelta(minutes=999)
    )
    result = evaluate_exit(
        signal=stale_signal,
        account=make_account(open_position_symbols=frozenset({"BTC/USDT"})),
        config=RiskConfig(),
        now=NOW,
        is_duplicate_decision=False,
    )
    assert result.approved is False
    assert result.rejection_reason == RejectionReason.STALE_SIGNAL


def test_exit_is_not_blocked_by_open_positions_on_other_symbols():
    result = evaluate_exit(
        signal=make_signal(action=Action.SELL, symbol="BTC/USDT"),
        account=make_account(open_position_symbols=frozenset({"ETH/USDT"})),
        config=RiskConfig(),
        now=NOW,
        is_duplicate_decision=False,
    )
    assert result.approved is False
    assert result.rejection_reason == RejectionReason.NO_POSITION_TO_EXIT
