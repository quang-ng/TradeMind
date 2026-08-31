from common.enums import RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rule 10 (PROJECT.md Section 9.1) — after `consecutive_loss_limit`
    consecutive losing closed positions (any pair), persistently halt new
    entries by auto-tripping the global kill switch.

    The existing operator kill-switch endpoint is the explicit reset path.
    An elapsed timer must not silently resume live trading after a repeated
    losing sequence.
    """
    if ctx.account.consecutive_losses < ctx.config.consecutive_loss_limit:
        return None
    return RuleViolation(
        reason=RejectionReason.CONSECUTIVE_LOSS_PAUSE,
        auto_trip_killswitch=True,
    )
