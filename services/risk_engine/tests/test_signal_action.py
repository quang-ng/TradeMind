from common.enums import Action, RejectionReason

from risk_engine.app.rules import signal_action

from .factories import make_context, make_signal


def test_passes_for_buy():
    ctx = make_context(signal=make_signal(action=Action.BUY))
    assert signal_action.check(ctx) is None


def test_passes_for_sell():
    ctx = make_context(signal=make_signal(action=Action.SELL))
    assert signal_action.check(ctx) is None


def test_rejects_hold():
    ctx = make_context(signal=make_signal(action=Action.HOLD))
    violation = signal_action.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.SIGNAL_WAS_HOLD
