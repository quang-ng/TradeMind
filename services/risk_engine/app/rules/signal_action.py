from common.enums import Action, RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rule 3 (PROJECT.md Section 9.1). `action = HOLD` is never "rejected"
    in the ordinary sense — it always resolves to `SIGNAL_WAS_HOLD` and
    generates no order, even if the signal is also stale or low-confidence
    (rules are short-circuited in Section 9.1's listed order)."""
    if ctx.signal.action == Action.HOLD:
        return RuleViolation(reason=RejectionReason.SIGNAL_WAS_HOLD)
    return None
