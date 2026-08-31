from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from common.config import RiskConfig
from common.enums import Action
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

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
    # Positive-expectancy plan M5 — the journal dimensions the expectancy
    # filter keys its historical-setup lookup on (`Signal.setup_regime` /
    # `Signal.trade_score`, added to the wire contract in M2). `None` for
    # signals produced before M2, or when scoring/classification failed —
    # the filter treats a missing key as "abstain", never "reject".
    setup_regime: str | None = None
    trade_score: int | None = None


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
class ExpectancyView:
    """Historical performance of the current signal's setup cohort
    (`setup_key` = regime + trade-score bucket), as of decision time
    (positive-expectancy plan M5). Resolved by
    `expectancy_state.load_expectancy_state` — which does the Postgres read
    and the `common.performance` math — *before* the pure `evaluate()` runs,
    exactly as `AccountState` is, so the rule set stays a pure function of
    its inputs (PROJECT.md Section 9).

    `sample_size` is the count of R-tracked closed trades in the cohort
    (trades with no `r_multiple` are excluded from both this and
    `expectancy_r`, never counted as 0R — plan D5). `expectancy_r` is
    `None` when the cohort has no R-tracked trade at all."""

    setup_key: str
    sample_size: int
    expectancy_r: Decimal | None


@dataclass(frozen=True)
class RuleContext:
    """Everything the pure rule set (PROJECT.md Section 9.1) needs to
    evaluate one signal. Ephemeral, Redis-sourced flags — kill switch,
    duplicate-decision idempotency — are resolved by the caller before this
    is constructed, so every rule function stays a pure, synchronous
    function of its inputs (Section 9: "The Risk Engine is a pure,
    deterministic function..."). `candidate` is the position size/stop that
    would be attached if the signal is ultimately approved — computed once,
    up front, so rules 8 and 12 can evaluate it without recomputing sizing
    mid-pipeline. `expectancy` is the current setup cohort's historical
    performance, pre-fetched the same way `account` is (positive-expectancy
    plan M5)."""

    signal: SignalView
    account: AccountState
    config: RiskConfig
    now: datetime
    killswitch_enabled: bool
    is_duplicate_decision: bool
    candidate: SizingResult
    expectancy: ExpectancyView


class FreqtradeTrade(BaseModel):
    """Typed subset of Freqtrade's trade response used for reconciliation."""

    model_config = ConfigDict(extra="ignore")

    trade_id: int = Field(validation_alias=AliasChoices("trade_id", "id"))
    pair: str
    is_open: bool
    amount: Decimal | None = None
    has_open_orders: bool | None = None
    open_rate: Decimal | None = None
    open_fill_date: datetime | None = None
    close_rate: Decimal | None = None
    enter_tag: str | None = None
    profit_abs: Decimal | None = None
    profit_ratio: Decimal | None = None
    open_date: datetime | None = None
    close_date: datetime | None = None


class FreqtradeCurrencyBalance(BaseModel):
    """Typed subset of one currency row from Freqtrade's `/balance` API."""

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    currency: str
    free: Decimal = Field(ge=0)
    balance: Decimal = Field(ge=0)
    used: Decimal = Field(ge=0)
    stake: str


class FreqtradeBalances(BaseModel):
    """Typed subset of Freqtrade's authenticated `/balance` response."""

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    currencies: list[FreqtradeCurrencyBalance]
    total: Decimal = Field(ge=0)
    stake: str
