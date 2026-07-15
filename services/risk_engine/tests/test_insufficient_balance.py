from decimal import Decimal

from common.enums import RejectionReason
from factories import make_account, make_context
from risk_engine.app.rules import insufficient_balance
from risk_engine.app.sizing import SizingResult


def test_passes_when_size_within_free_balance():
    ctx = make_context()
    assert insufficient_balance.check(ctx) is None


def test_rejects_when_computed_size_exceeds_free_balance():
    # Sizing (Section 9.2) already clamps position_size_usdt to
    # free_balance_usdt, so under normal computation this rule can never
    # fire — it's a deliberate defense-in-depth check. Exercise it by
    # injecting a candidate that is inconsistent with sizing's own
    # invariant, simulating what this rule guards against.
    account = make_account(free_balance_usdt=Decimal("100"))
    bad_candidate = SizingResult(
        position_size_usdt=Decimal("500"),
        position_size_base=Decimal("0.0083"),
        stop_loss_price=Decimal("59000"),
        stop_distance_pct=Decimal("0.0167"),
        risk_pct_applied=Decimal("0.01"),
    )
    ctx = make_context(account=account, candidate=bad_candidate)
    violation = insufficient_balance.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.INSUFFICIENT_BALANCE
