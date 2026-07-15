from common.enums import RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rule 6 (PROJECT.md Section 9.1) — reject if the pair already has an
    open position, or total open positions have reached the limit."""
    already_open = ctx.signal.symbol in ctx.account.open_position_symbols
    at_limit = len(ctx.account.open_position_symbols) >= ctx.config.max_open_positions
    if already_open or at_limit:
        return RuleViolation(reason=RejectionReason.MAX_POSITIONS_REACHED)
    return None
