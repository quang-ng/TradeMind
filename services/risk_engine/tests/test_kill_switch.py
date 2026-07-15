from common.enums import RejectionReason
from factories import make_context
from risk_engine.app.rules import kill_switch


def test_passes_when_killswitch_disabled():
    ctx = make_context(killswitch_enabled=False)
    assert kill_switch.check(ctx) is None


def test_rejects_when_killswitch_enabled():
    ctx = make_context(killswitch_enabled=True)
    violation = kill_switch.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.KILLSWITCH_ACTIVE
