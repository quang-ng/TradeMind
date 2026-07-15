from common.enums import RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rule 11 (PROJECT.md Section 9.1). Sizing (Section 9.2) already clamps
    `position_size_usdt` to `free_balance_usdt`, so in normal operation this
    can only fire if that invariant is ever violated — a deliberate,
    independent, defense-in-depth check rather than the primary gate."""
    if ctx.candidate.position_size_usdt > ctx.account.free_balance_usdt:
        return RuleViolation(reason=RejectionReason.INSUFFICIENT_BALANCE)
    return None
