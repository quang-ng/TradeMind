from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from common.config import RiskConfig
from common.enums import RejectionReason

from .schemas import AccountState, SignalView


@dataclass(frozen=True)
class ExitDecisionResult:
    approved: bool
    rejection_reason: RejectionReason | None
    equity_snapshot_usdt: Decimal


def evaluate_exit(
    *,
    signal: SignalView,
    account: AccountState,
    config: RiskConfig,
    now: datetime,
    is_duplicate_decision: bool,
) -> ExitDecisionResult:
    """A `SELL` signal closes an existing open position (`forceexit`)
    rather than opening a short — the system is long-only (PROJECT.md
    Section 2.2). This is a deliberately lighter gate than
    `evaluator.evaluate()`'s entry pipeline (Section 9.1):

    - No kill-switch check: the kill switch halts new *entries*
      (PROJECT.md Section 11/13 — "blocks every subsequent entry"); it
      must never block getting *out* of risk.
    - No minimum-confidence check: exits reduce risk, so the bias here is
      toward allowing them, not gating them the way new exposure is
      gated.
    - Duplicate-signal idempotency and staleness are still checked — both
      are correctness concerns (don't double-process a redelivered
      message, don't act on stale market data), not risk-tolerance
      concerns.

    If there is no open position for the signal's symbol, the exit is a
    no-op and is rejected with `NO_POSITION_TO_EXIT` rather than doing
    nothing silently (PROJECT.md Section 5.1: "silence is never a valid
    outcome of a cycle").
    """
    if is_duplicate_decision:
        return ExitDecisionResult(
            approved=False,
            rejection_reason=RejectionReason.DUPLICATE_SIGNAL,
            equity_snapshot_usdt=account.equity_usdt,
        )

    max_age = timedelta(minutes=config.signal_max_age_minutes)
    if now - signal.candle_ts > max_age:
        return ExitDecisionResult(
            approved=False,
            rejection_reason=RejectionReason.STALE_SIGNAL,
            equity_snapshot_usdt=account.equity_usdt,
        )

    if signal.symbol not in account.open_position_symbols:
        return ExitDecisionResult(
            approved=False,
            rejection_reason=RejectionReason.NO_POSITION_TO_EXIT,
            equity_snapshot_usdt=account.equity_usdt,
        )

    return ExitDecisionResult(
        approved=True, rejection_reason=None, equity_snapshot_usdt=account.equity_usdt
    )
