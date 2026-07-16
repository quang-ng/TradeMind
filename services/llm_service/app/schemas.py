from typing import Annotated, Literal

from common.enums import Action, SignalStatus
from common.sentiment import MarketSentiment
from pydantic import BaseModel, Field, StringConstraints

# "BASE/QUOTE" shape only (e.g. "BTC/USDT") — deliberately not a fixed
# Literal enum of specific coins. The active symbol set lives in one place,
# SchedulerSettings.symbols (common/config.py, SYMBOLS env var), so enabling
# or disabling a symbol there never requires a matching schema/code change
# here.
SymbolStr = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]+/[A-Z0-9]+$")]


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


class ProviderOverride(BaseModel):
    """Effective LLM config computed by the Scheduler from
    `common.llm_config_store` (env defaults + any persisted
    `PATCH /config/llm`) and forwarded per-request, since `llm_service`
    itself has no DB access (PROJECT.md Section 3: Isolated Zone). Absent
    fields fall back to this service's own env-sourced `LLMServiceSettings`."""

    llm_provider: Literal["anthropic", "ollama"] | None = None
    anthropic_model: str | None = None
    ollama_model: str | None = None
    ollama_temperature: float | None = None


class AnalyzeRequest(BaseModel):
    symbol: SymbolStr
    # "1h" is the intended live-trading cadence (PROJECT.md Section 2.1);
    # "5m" is the scheduler's demo/dry-run default (SchedulerSettings.timeframe).
    timeframe: Literal["1h", "5m"]
    candle_close_time: str
    ohlcv: list[Candle]
    indicators: Indicators
    sentiment: MarketSentiment | None = None
    position_context: PositionContext
    provider_override: ProviderOverride | None = None


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
