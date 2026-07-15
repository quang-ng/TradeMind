from datetime import timedelta

from common.enums import RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rule 9 (PROJECT.md Section 9.1) — after `consecutive_loss_limit`
    consecutive losing closed positions (any pair), pause all new entries
    for `cooldown_minutes` starting from the most recent loss's close."""
    if ctx.account.consecutive_losses < ctx.config.consecutive_loss_limit:
        return None
    if ctx.account.last_loss_closed_at is None:
        return None
    pause_until = ctx.account.last_loss_closed_at + timedelta(minutes=ctx.config.cooldown_minutes)
    if ctx.now < pause_until:
        return RuleViolation(reason=RejectionReason.CONSECUTIVE_LOSS_PAUSE)
    return None
