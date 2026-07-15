from datetime import timedelta

from common.enums import RejectionReason

from risk_engine.app.rules import signal_staleness

from .factories import NOW, make_context, make_signal


def test_passes_for_fresh_signal():
    ctx = make_context(signal=make_signal(candle_ts=NOW - timedelta(minutes=1)))
    assert signal_staleness.check(ctx) is None


def test_rejects_stale_signal():
    ctx = make_context(signal=make_signal(candle_ts=NOW - timedelta(minutes=11)))
    violation = signal_staleness.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.STALE_SIGNAL
