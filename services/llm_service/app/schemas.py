from typing import Literal

from common.enums import Action, SignalStatus
from pydantic import BaseModel, Field


class Candle(BaseModel):
    t: str
    o: float
    h: float
    l: float  # noqa: E741 -- wire field name fixed by PROJECT.md Section 8.1
    c: float
    v: float


class MACD(BaseModel):
    macd: float
    signal: float
    histogram: float


class Indicators(BaseModel):
    rsi_14: float
    ema_50: float
    ema_200: float
    macd: MACD
    atr_14: float
    volume_sma_20: float


class PositionContext(BaseModel):
    has_open_position: bool
    unrealized_pnl_pct: float | None = None


class AnalyzeRequest(BaseModel):
    symbol: Literal["BTC/USDT", "ETH/USDT"]
    timeframe: Literal["1h"]
    candle_close_time: str
    ohlcv: list[Candle]
    indicators: Indicators
    position_context: PositionContext


class LLMOutput(BaseModel):
    """Raw model contract, Section 8.2 of PROJECT.md."""

    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1, max_length=500)
    key_indicators: list[str] = Field(default_factory=list)
    invalidation_condition: str = Field(min_length=1)


class Signal(BaseModel):
    """Validated result of one /analyze call. Mirrors the Section 7.1 Signal
    fields that are determinable without persistence (id/trace_id are minted
    by the Scheduler + Postgres in a later phase)."""

    symbol: str
    timeframe: str
    candle_ts: str
    action: Action
    confidence: float
    reasoning: str
    model_name: str
    raw_response: dict | None = None
    status: SignalStatus = SignalStatus.PENDING
