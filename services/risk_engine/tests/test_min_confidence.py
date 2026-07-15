from decimal import Decimal

from common.enums import RejectionReason

from risk_engine.app.rules import min_confidence

from .factories import make_context, make_signal


def test_passes_above_threshold():
    ctx = make_context(signal=make_signal(confidence=Decimal("0.80")))
    assert min_confidence.check(ctx) is None


def test_rejects_below_threshold():
    ctx = make_context(signal=make_signal(confidence=Decimal("0.50")))
    violation = min_confidence.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.LOW_CONFIDENCE
