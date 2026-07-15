from common.enums import RejectionReason
from factories import make_context
from risk_engine.app.rules import duplicate_signal


def test_passes_when_not_a_duplicate():
    ctx = make_context(is_duplicate_decision=False)
    assert duplicate_signal.check(ctx) is None


def test_rejects_when_duplicate():
    ctx = make_context(is_duplicate_decision=True)
    violation = duplicate_signal.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.DUPLICATE_SIGNAL
