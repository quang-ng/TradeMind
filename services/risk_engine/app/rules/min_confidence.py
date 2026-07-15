from common.enums import RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rule 5 (PROJECT.md Section 9.1)."""
    if ctx.signal.confidence < ctx.config.min_confidence:
        return RuleViolation(reason=RejectionReason.LOW_CONFIDENCE)
    return None
