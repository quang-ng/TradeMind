from common.enums import RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rule 2 (PROJECT.md Section 9.1). Rejected silently — no duplicate
    Telegram notification — but still recorded as a RiskDecision row."""
    if ctx.is_duplicate_decision:
        return RuleViolation(reason=RejectionReason.DUPLICATE_SIGNAL)
    return None
