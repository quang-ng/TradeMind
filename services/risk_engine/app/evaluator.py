from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from common.config import RiskConfig
from common.enums import RejectionReason

from . import sizing
from .rules import RULES_IN_ORDER
from .rules.expectancy_filter import ExpectancyCheck, build_expectancy_check
from .schemas import AccountState, ExpectancyView, RuleContext, SignalView


@dataclass(frozen=True)
class RiskDecisionResult:
    approved: bool
    rejection_reason: RejectionReason | None
    equity_snapshot_usdt: Decimal
    position_size_usdt: Decimal | None = None
    position_size_base: Decimal | None = None
    stop_loss_price: Decimal | None = None
    stop_distance_pct: Decimal | None = None
    risk_pct_applied: Decimal | None = None
    nominal_risk_amount_usdt: Decimal | None = None
    actual_risk_usdt: Decimal | None = None
    auto_trip_killswitch: bool = False
    # Positive-expectancy plan M5 (D4): the Historical Expectancy Filter's
    # shadow verdict for this signal — "what the filter would have decided"
    # — computed on every evaluated entry regardless of approve/reject or
    # whether the filter is enabled, so `main.py` can audit it from day one.
    # `None` only on the Section 9.4 INTERNAL_ERROR fallback path.
    expectancy_check: ExpectancyCheck | None = None


def evaluate(
    *,
    signal: SignalView,
    account: AccountState,
    config: RiskConfig,
    now: datetime,
    killswitch_enabled: bool,
    is_duplicate_decision: bool,
    expectancy: ExpectancyView,
) -> RiskDecisionResult:
    """PROJECT.md Section 9 — pure, deterministic
    `(signal, account_state, risk_config) -> RiskDecision`. No I/O, no LLM
    calls, no non-determinism. Rules run in Section 9.1's fixed order and
    short-circuit on the first failure.

    `expectancy` is the current setup cohort's historical performance,
    resolved by `expectancy_state.load_expectancy_state` before this call
    the same way `account` is — the expectancy filter (rule 6) and the
    shadow `expectancy_check` both read it without any I/O of their own."""
    candidate = sizing.compute_sizing(
        equity_usdt=account.equity_usdt,
        free_balance_usdt=account.free_balance_usdt,
        entry_price=signal.price,
        atr_14=signal.atr_14,
        confidence=signal.confidence,
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
        expectancy=expectancy,
    )

    # Computed up front so it lands in the audit payload on every evaluated
    # entry — approved, or rejected by any rule, filter enabled or not (D4).
    expectancy_check = build_expectancy_check(ctx)

    for rule in RULES_IN_ORDER:
        violation = rule(ctx)
        if violation is not None:
            return RiskDecisionResult(
                approved=False,
                rejection_reason=violation.reason,
                equity_snapshot_usdt=account.equity_usdt,
                auto_trip_killswitch=violation.auto_trip_killswitch,
                expectancy_check=expectancy_check,
            )

    return RiskDecisionResult(
        approved=True,
        rejection_reason=None,
        equity_snapshot_usdt=account.equity_usdt,
        position_size_usdt=candidate.position_size_usdt,
        position_size_base=candidate.position_size_base,
        stop_loss_price=candidate.stop_loss_price,
        stop_distance_pct=candidate.stop_distance_pct,
        risk_pct_applied=candidate.risk_pct_applied,
        nominal_risk_amount_usdt=candidate.nominal_risk_amount_usdt,
        actual_risk_usdt=candidate.actual_risk_usdt,
        expectancy_check=expectancy_check,
    )
