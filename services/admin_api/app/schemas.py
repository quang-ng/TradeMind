import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FreqtradeWebhookPayload(BaseModel):
    """PROJECT.md Section 11 `POST /webhooks/freqtrade`. Field set matches
    `freqtrade/user_data/config.json.tpl`'s webhook templates — Freqtrade
    renders every templated value as a JSON string, so the numeric fields
    below rely on Pydantic's str -> Decimal/int coercion."""

    event: str
    trade_id: int
    pair: str
    secret: str
    open_rate: Decimal | None = None
    amount: Decimal | None = None
    open_date: str | None = None
    close_rate: Decimal | None = None
    profit_amount: Decimal | None = None
    profit_ratio: Decimal | None = None
    close_date: str | None = None


# --- Read models (PROJECT.md Section 7) -----------------------------------
# `from_attributes=True` lets these be built straight from the SQLAlchemy
# ORM rows returned by the routers (AGENTS.md Section 3: "Pydantic at every
# boundary... no raw dict crossing a service boundary").


class SignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trace_id: uuid.UUID
    symbol: str
    timeframe: str
    candle_ts: datetime
    action: str
    confidence: Decimal
    reasoning: str
    model_name: str
    price: Decimal
    atr_14: Decimal
    status: str
    created_at: datetime


class SignalDetailOut(SignalOut):
    raw_response: dict[str, Any] | None = None
    model_input: dict[str, Any] | None = None


class RiskDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trace_id: uuid.UUID
    signal_id: uuid.UUID
    approved: bool
    rejection_reason: str | None
    position_size_usdt: Decimal | None
    position_size_base: Decimal | None
    stop_loss_price: Decimal | None
    equity_snapshot_usdt: Decimal
    risk_pct_applied: Decimal | None
    created_at: datetime


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trace_id: uuid.UUID
    risk_decision_id: uuid.UUID
    freqtrade_trade_id: int | None
    symbol: str
    side: str
    status: str
    requested_amount: Decimal
    filled_amount: Decimal | None
    avg_price: Decimal | None
    dry_run: bool
    created_at: datetime
    updated_at: datetime


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol: str
    status: str
    entry_order_id: uuid.UUID
    exit_order_id: uuid.UUID | None
    entry_price: Decimal
    exit_price: Decimal | None
    amount: Decimal
    pnl_usdt: Decimal | None
    pnl_pct: Decimal | None
    opened_at: datetime
    closed_at: datetime | None


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trace_id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class AuditTimelineOut(BaseModel):
    """PROJECT.md Section 11 `GET /audit?trace_id=` — "Full timeline for one
    trading cycle". One `trace_id` covers one Signal -> RiskDecision ->
    Order(s) run (Section 7's opening paragraph); a position's eventual
    close is a *different* trace_id (the SELL signal's own cycle), so this
    intentionally does not try to also embed Position."""

    trace_id: uuid.UUID
    signals: list[SignalDetailOut]
    risk_decisions: list[RiskDecisionOut]
    orders: list[OrderOut]
    audit_events: list[AuditEventOut]


# --- Status ------------------------------------------------------------


class PairStatus(BaseModel):
    last_cycle_at: datetime | None
    last_action: str | None


class StatusOut(BaseModel):
    killswitch_enabled: bool
    dry_run: bool
    open_positions: int
    equity_usdt: Decimal
    daily_pnl_pct: Decimal
    pairs: dict[str, PairStatus]


# --- Kill switch ---------------------------------------------------------


class KillswitchRequest(BaseModel):
    reason: str
    # Defaults to "api:admin" server-side when omitted (PROJECT.md Section
    # 11 example). The notifier passes "telegram:<chat_id>" explicitly —
    # "Telegram is a client of the API, not a parallel control path".
    updated_by: str | None = None


class KillswitchResponse(BaseModel):
    killswitch_enabled: bool
    updated_by: str | None
    updated_at: datetime


# --- Risk config (PROJECT.md Section 9.1) ---------------------------------


class RiskConfigOut(BaseModel):
    risk_per_trade_pct: Decimal
    max_position_pct: Decimal
    max_total_exposure_pct: Decimal
    max_open_positions: int
    max_daily_loss_pct: Decimal
    consecutive_loss_limit: int
    cooldown_minutes: int
    min_confidence: Decimal
    signal_max_age_minutes: int
    atr_stop_multiplier: Decimal
    min_stop_loss_pct: Decimal
    max_stop_loss_pct: Decimal
    dry_run: bool


class RiskConfigPatch(BaseModel):
    risk_per_trade_pct: Decimal | None = None
    max_position_pct: Decimal | None = None
    max_total_exposure_pct: Decimal | None = None
    max_open_positions: int | None = None
    max_daily_loss_pct: Decimal | None = None
    consecutive_loss_limit: int | None = None
    cooldown_minutes: int | None = None
    min_confidence: Decimal | None = None
    signal_max_age_minutes: int | None = None
    atr_stop_multiplier: Decimal | None = None
    min_stop_loss_pct: Decimal | None = None
    max_stop_loss_pct: Decimal | None = None
    dry_run: bool | None = None
    # PROJECT.md Section 14 rule 13: flipping `dry_run` is a deliberate
    # human decision, gated by explicit confirmation in this flow.
    confirm_dry_run_change: bool = False


# --- LLM config (PROJECT.md Section 8.4) ----------------------------------


class LLMConfigOut(BaseModel):
    llm_provider: str
    anthropic_model: str
    ollama_model: str
    ollama_temperature: float


class LLMConfigPatch(BaseModel):
    llm_provider: Literal["anthropic", "ollama"] | None = None
    anthropic_model: str | None = None
    ollama_model: str | None = None
    ollama_temperature: float | None = Field(default=None, ge=0.0, le=2.0)


# --- Manual cycle trigger --------------------------------------------------


class CycleTriggerResponse(BaseModel):
    trace_id: uuid.UUID | None
    skipped: bool
