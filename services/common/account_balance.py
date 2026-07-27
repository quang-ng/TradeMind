from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class AccountBalanceSnapshot(BaseModel):
    """Short-lived, typed Freqtrade balance snapshot shared through Redis."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    equity_usdt: Decimal = Field(ge=0)
    free_balance_usdt: Decimal = Field(ge=0)
    captured_at: AwareDatetime
    source: Literal["freqtrade"] = "freqtrade"

    @model_validator(mode="after")
    def _free_balance_cannot_exceed_equity(self) -> "AccountBalanceSnapshot":
        if self.free_balance_usdt > self.equity_usdt:
            raise ValueError("free balance cannot exceed account equity")
        return self

    def is_fresh(self, *, now: datetime, max_age_seconds: int) -> bool:
        return 0 <= (now - self.captured_at).total_seconds() <= max_age_seconds
