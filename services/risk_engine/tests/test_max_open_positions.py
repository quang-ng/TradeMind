from common.enums import RejectionReason
from factories import make_account, make_context
from risk_engine.app.rules import max_open_positions


def test_passes_when_below_limit_and_pair_not_open():
    ctx = make_context(account=make_account(open_position_symbols=frozenset()))
    assert max_open_positions.check(ctx) is None


def test_rejects_when_pair_already_open():
    ctx = make_context(account=make_account(open_position_symbols=frozenset({"BTC/USDT"})))
    violation = max_open_positions.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.MAX_POSITIONS_REACHED


def test_rejects_when_total_open_positions_at_limit():
    account = make_account(open_position_symbols=frozenset({"ETH/USDT", "SOL/USDT"}))
    ctx = make_context(account=account)
    violation = max_open_positions.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.MAX_POSITIONS_REACHED
