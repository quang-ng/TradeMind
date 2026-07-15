from common.enums import RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rule 8 (PROJECT.md Section 9.1) — the daily-loss circuit breaker.
    Violating this rule also auto-enables the global kill switch (`SYSTEM`
    actor); the caller (evaluator.py) is responsible for performing that
    side effect, since rule functions stay pure."""
    if ctx.account.daily_pnl_pct <= -ctx.config.max_daily_loss_pct:
        return RuleViolation(reason=RejectionReason.DAILY_LOSS_LIMIT_HIT, auto_trip_killswitch=True)
    return None
