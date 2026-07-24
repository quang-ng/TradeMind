"""The `/analyze` HTTP contract (PROJECT.md Section 8). Every field name and
shape here is load-bearing for the Scheduler, so nothing in this module may
change without a matching PROJECT.md update — that is the one rule the rest
of this package's internal reorganization does not get to break."""

from typing import Annotated, Literal

from common.enums import Action, SignalStatus
from common.sentiment import MarketSentiment
from pydantic import BaseModel, StringConstraints

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
    # "30m" is the scheduler's current default (SchedulerSettings.timeframe);
    # "5m" remains for backwards compatibility with older persisted signals.
    timeframe: Literal["1h", "30m", "5m"]
    candle_close_time: str
    ohlcv: list[Candle]
    indicators: Indicators
    sentiment: MarketSentiment | None = None
    position_context: PositionContext
    provider_override: ProviderOverride | None = None


class TradingSignal(BaseModel):
    """Validated result of one `/analyze` call — the HTTP response model.

    Named `TradingSignal` (not `Signal`) to distinguish it, inside this
    codebase, from `common.db.models.Signal`, the SQLAlchemy row the
    Scheduler persists from this response. The two have historically shared
    a name; this one is the wire representation, minted fresh per request,
    with no `id`/`trace_id` (those are minted by the Scheduler + Postgres
    downstream). Field names and shapes mirror PROJECT.md Section 7.1
    exactly — renaming the Python class does not change the JSON produced,
    which is what the Scheduler actually depends on.
    """

    symbol: str
    timeframe: str
    candle_ts: str
    action: Action
    confidence: float
    reasoning: str
    model_name: str
    raw_response: dict | None = None
    status: SignalStatus = SignalStatus.PENDING
