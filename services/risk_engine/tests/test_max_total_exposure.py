from decimal import Decimal

from common.enums import RejectionReason

from risk_engine.app.rules import max_total_exposure

from .factories import make_account, make_context


def test_passes_when_within_exposure_cap():
    # default context sizes a ~500 USDT candidate against a 10000 equity
    # account (20% cap = 2000 USDT); 0 existing exposure leaves headroom.
    ctx = make_context(account=make_account(total_exposure_usdt=Decimal("0")))
    assert max_total_exposure.check(ctx) is None


def test_rejects_when_adding_candidate_would_exceed_cap():
    ctx = make_context(account=make_account(total_exposure_usdt=Decimal("1800")))
    violation = max_total_exposure.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.MAX_EXPOSURE_REACHED
