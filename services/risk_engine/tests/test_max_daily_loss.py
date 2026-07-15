from decimal import Decimal

from common.enums import RejectionReason
from factories import make_account, make_context
from risk_engine.app.rules import max_daily_loss


def test_passes_when_daily_loss_within_limit():
    ctx = make_context(account=make_account(daily_pnl_pct=Decimal("-0.01")))
    assert max_daily_loss.check(ctx) is None


def test_rejects_and_flags_auto_trip_at_limit():
    ctx = make_context(account=make_account(daily_pnl_pct=Decimal("-0.03")))
    violation = max_daily_loss.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.DAILY_LOSS_LIMIT_HIT
    assert violation.auto_trip_killswitch is True


def test_rejects_when_daily_loss_exceeds_limit():
    ctx = make_context(account=make_account(daily_pnl_pct=Decimal("-0.10")))
    violation = max_daily_loss.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.DAILY_LOSS_LIMIT_HIT
