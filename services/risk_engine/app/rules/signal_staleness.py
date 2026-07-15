from datetime import timedelta

from common.enums import RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rule 4 (PROJECT.md Section 9.1)."""
    max_age = timedelta(minutes=ctx.config.signal_max_age_minutes)
    if ctx.now - ctx.signal.candle_ts > max_age:
        return RuleViolation(reason=RejectionReason.STALE_SIGNAL)
    return None
