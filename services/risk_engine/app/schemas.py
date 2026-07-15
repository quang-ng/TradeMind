from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from common.config import RiskConfig
from common.enums import Action

from .sizing import SizingResult


@dataclass(frozen=True)
class SignalView:
    """The subset of a persisted Signal (PROJECT.md Section 7.1) the rule
    set and sizing formula need."""

    id: str
    symbol: str
    action: Action
    confidence: Decimal
    candle_ts: datetime
    price: Decimal
    atr_14: Decimal


@dataclass(frozen=True)
class AccountState:
    """Account/portfolio state as of decision time (PROJECT.md Section 9.1 /
    9.2 inputs). All monetary fields are `Decimal` (PROJECT.md Section 14
    rule 10)."""

    equity_usdt: Decimal
    free_balance_usdt: Decimal
    open_position_symbols: frozenset[str]
    total_exposure_usdt: Decimal
    daily_pnl_pct: Decimal
    consecutive_losses: int
    last_loss_closed_at: datetime | None
    symbol_last_closed_at: dict[str, datetime] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleContext:
    """Everything the pure rule set (PROJECT.md Section 9.1) needs to
    evaluate one signal. Ephemeral, Redis-sourced flags — kill switch,
    duplicate-decision idempotency — are resolved by the caller before this
    is constructed, so every rule function stays a pure, synchronous
    function of its inputs (Section 9: "The Risk Engine is a pure,
    deterministic function..."). `candidate` is the position size/stop that
    would be attached if the signal is ultimately approved — computed once,
    up front, so rules 7 and 11 can evaluate it without recomputing sizing
    mid-pipeline."""

    signal: SignalView
    account: AccountState
    config: RiskConfig
    now: datetime
    killswitch_enabled: bool
    is_duplicate_decision: bool
    candidate: SizingResult
