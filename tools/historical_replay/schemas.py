from datetime import datetime
from decimal import Decimal

from common.config import RiskConfig
from common.enums import Action, RejectionReason
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Candle(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(pattern=r"^[A-Z0-9]+/[A-Z0-9]+$")
    open_time: datetime
    close_time: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)

    @field_validator("open_time", "close_time")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candle timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> "Candle":
        if self.close_time <= self.open_time:
            raise ValueError("close_time must be after open_time")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")
        if self.low > self.high:
            raise ValueError("low must not exceed high")
        return self


class SyntheticSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(pattern=r"^[A-Z0-9]+/[A-Z0-9]+$")
    candle_close_time: datetime
    action: Action
    confidence: Decimal = Field(ge=0, le=1)
    atr_14: Decimal = Field(gt=0)
    reasoning: str = "synthetic replay signal"

    @field_validator("candle_close_time")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("signal timestamp must be timezone-aware")
        return value


class ReplayConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    starting_equity_usdt: Decimal = Field(default=Decimal("10000"), gt=0)
    fee_rate: Decimal = Field(default=Decimal("0.001"), ge=0, lt=1)
    slippage_rate: Decimal = Field(default=Decimal("0.0005"), ge=0, lt=1)
    static_stop_loss_pct: Decimal = Field(default=Decimal("0.08"), gt=0, lt=1)
    risk: RiskConfig = Field(default_factory=RiskConfig)


class ReplayDecision(BaseModel):
    symbol: str
    candle_close_time: datetime
    action: Action
    confidence: Decimal
    approved: bool
    rejection_reason: RejectionReason | None = None
    pending_side: str | None = None


class ReplayTrade(BaseModel):
    symbol: str
    opened_at: datetime
    closed_at: datetime
    entry_reference_price: Decimal
    entry_price: Decimal
    exit_reference_price: Decimal
    exit_price: Decimal
    amount: Decimal
    stake_usdt: Decimal
    entry_fee_usdt: Decimal
    exit_fee_usdt: Decimal
    gross_pnl_usdt: Decimal
    net_pnl_usdt: Decimal
    net_pnl_pct: Decimal
    exit_reason: str


class EquityPoint(BaseModel):
    timestamp: datetime
    equity_usdt: Decimal


class ReplaySummary(BaseModel):
    starting_equity_usdt: Decimal
    ending_equity_usdt: Decimal
    net_pnl_usdt: Decimal
    net_return_pct: Decimal
    gross_pnl_usdt: Decimal
    fees_usdt: Decimal
    trades: int
    wins: int
    losses: int
    win_rate: Decimal
    profit_factor: Decimal | None
    expectancy_usdt: Decimal
    max_drawdown_pct: Decimal
    open_positions: int
    pending_orders: int
    rejection_counts: dict[str, int]


class ReplayResult(BaseModel):
    config: ReplayConfig
    summary: ReplaySummary
    trades: list[ReplayTrade]
    decisions: list[ReplayDecision]
    equity_curve: list[EquityPoint]
