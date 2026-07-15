from datetime import timedelta

from common.enums import RejectionReason

from risk_engine.app.rules import cooldown

from .factories import NOW, make_account, make_context, make_signal


def test_passes_when_pair_never_closed():
    ctx = make_context(account=make_account(symbol_last_closed_at={}))
    assert cooldown.check(ctx) is None


def test_rejects_within_cooldown_window():
    account = make_account(symbol_last_closed_at={"BTC/USDT": NOW - timedelta(minutes=10)})
    ctx = make_context(signal=make_signal(symbol="BTC/USDT"), account=account)
    violation = cooldown.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.COOLDOWN_ACTIVE


def test_passes_once_cooldown_window_has_elapsed():
    account = make_account(symbol_last_closed_at={"BTC/USDT": NOW - timedelta(minutes=200)})
    ctx = make_context(signal=make_signal(symbol="BTC/USDT"), account=account)
    assert cooldown.check(ctx) is None


def test_unaffected_by_other_symbols_cooldown():
    account = make_account(symbol_last_closed_at={"ETH/USDT": NOW - timedelta(minutes=10)})
    ctx = make_context(signal=make_signal(symbol="BTC/USDT"), account=account)
    assert cooldown.check(ctx) is None
