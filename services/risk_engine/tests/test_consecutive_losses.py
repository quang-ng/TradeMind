from datetime import timedelta

from common.enums import RejectionReason

from risk_engine.app.rules import consecutive_losses

from .factories import NOW, make_account, make_context


def test_passes_when_below_loss_limit():
    ctx = make_context(account=make_account(consecutive_losses=2, last_loss_closed_at=NOW))
    assert consecutive_losses.check(ctx) is None


def test_rejects_within_cooldown_window_after_limit_reached():
    account = make_account(
        consecutive_losses=3, last_loss_closed_at=NOW - timedelta(minutes=10)
    )
    ctx = make_context(account=account)
    violation = consecutive_losses.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.CONSECUTIVE_LOSS_PAUSE
    assert violation.auto_trip_killswitch is True


def test_remains_blocked_after_cooldown_until_operator_resets_killswitch():
    account = make_account(
        consecutive_losses=3, last_loss_closed_at=NOW - timedelta(minutes=200)
    )
    ctx = make_context(account=account)
    violation = consecutive_losses.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.CONSECUTIVE_LOSS_PAUSE
    assert violation.auto_trip_killswitch is True
