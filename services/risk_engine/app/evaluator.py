from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from common.config import RiskConfig
from common.enums import RejectionReason

from . import sizing
from .rules import RULES_IN_ORDER
from .schemas import AccountState, RuleContext, SignalView


@dataclass(frozen=True)
class RiskDecisionResult:
    approved: bool
    rejection_reason: RejectionReason | None
    equity_snapshot_usdt: Decimal
    position_size_usdt: Decimal | None = None
    position_size_base: Decimal | None = None
    stop_loss_price: Decimal | None = None
    risk_pct_applied: Decimal | None = None
    auto_trip_killswitch: bool = False


def evaluate(
    *,
    signal: SignalView,
    account: AccountState,
    config: RiskConfig,
    now: datetime,
    killswitch_enabled: bool,
    is_duplicate_decision: bool,
) -> RiskDecisionResult:
    """PROJECT.md Section 9 — pure, deterministic
    `(signal, account_state, risk_config) -> RiskDecision`. No I/O, no LLM
    calls, no non-determinism. Rules run in Section 9.1's fixed order and
    short-circuit on the first failure."""
    candidate = sizing.compute_sizing(
        equity_usdt=account.equity_usdt,
        free_balance_usdt=account.free_balance_usdt,
        entry_price=signal.price,
        atr_14=signal.atr_14,
        config=config,
    )
    ctx = RuleContext(
        signal=signal,
        account=account,
        config=config,
        now=now,
        killswitch_enabled=killswitch_enabled,
        is_duplicate_decision=is_duplicate_decision,
        candidate=candidate,
    )

    for rule in RULES_IN_ORDER:
        violation = rule(ctx)
        if violation is not None:
            return RiskDecisionResult(
                approved=False,
                rejection_reason=violation.reason,
                equity_snapshot_usdt=account.equity_usdt,
                auto_trip_killswitch=violation.auto_trip_killswitch,
            )

    return RiskDecisionResult(
        approved=True,
        rejection_reason=None,
        equity_snapshot_usdt=account.equity_usdt,
        position_size_usdt=candidate.position_size_usdt,
        position_size_base=candidate.position_size_base,
        stop_loss_price=candidate.stop_loss_price,
        risk_pct_applied=candidate.risk_pct_applied,
    )
