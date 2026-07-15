"""One module per rule row in PROJECT.md Section 9.1. `RULES_IN_ORDER` is
the single source of truth for evaluation order — rule 1 (kill switch) must
stay first (PROJECT.md Section 14 rule 4); the rest must stay in the exact
order Section 9.1 lists them, since the first failing rule determines the
rejection reason (short-circuit, no partial credit)."""

from . import (
    consecutive_losses,
    cooldown,
    duplicate_signal,
    insufficient_balance,
    kill_switch,
    max_daily_loss,
    max_open_positions,
    max_total_exposure,
    min_confidence,
    signal_action,
    signal_staleness,
)
from .base import RuleFunc, RuleViolation

RULES_IN_ORDER: list[RuleFunc] = [
    kill_switch.check,
    duplicate_signal.check,
    signal_action.check,
    signal_staleness.check,
    min_confidence.check,
    max_open_positions.check,
    max_total_exposure.check,
    max_daily_loss.check,
    consecutive_losses.check,
    cooldown.check,
    insufficient_balance.check,
]

__all__ = ["RULES_IN_ORDER", "RuleFunc", "RuleViolation"]
