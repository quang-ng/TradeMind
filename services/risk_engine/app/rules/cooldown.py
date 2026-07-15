from datetime import timedelta

from common.enums import RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rule 10 (PROJECT.md Section 9.1) — per-pair cooldown after a position
    on this pair closes, regardless of win/loss."""
    last_closed = ctx.account.symbol_last_closed_at.get(ctx.signal.symbol)
    if last_closed is None:
        return None
    if ctx.now - last_closed < timedelta(minutes=ctx.config.cooldown_minutes):
        return RuleViolation(reason=RejectionReason.COOLDOWN_ACTIVE)
    return None
