from common.enums import RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rule 1 (PROJECT.md Section 9.1) — always the first gate evaluated.
    Never reorder this ahead of anything else (PROJECT.md Section 14 rule
    4; AGENTS.md Section 7)."""
    if ctx.killswitch_enabled:
        return RuleViolation(reason=RejectionReason.KILLSWITCH_ACTIVE)
    return None
