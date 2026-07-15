from common.enums import RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rule 7 (PROJECT.md Section 9.1) — reject if adding the candidate
    position (Section 9.2 sizing, computed up front) would push total
    exposure past the cap."""
    prospective_total = ctx.account.total_exposure_usdt + ctx.candidate.position_size_usdt
    cap = ctx.config.max_total_exposure_pct * ctx.account.equity_usdt
    if prospective_total > cap:
        return RuleViolation(reason=RejectionReason.MAX_EXPOSURE_REACHED)
    return None
