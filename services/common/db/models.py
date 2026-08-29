import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from common.db import Base
from common.enums import (
    Action,
    AuditEventType,
    OrderSide,
    OrderStatus,
    PositionStatus,
    RejectionReason,
    SignalStatus,
)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Signal(Base):
    """PROJECT.md Section 7.1.

    `price` and `atr_14` are not in the Section 7.1 table as originally
    written; they are added here because Section 9.2's position-sizing
    formula needs the entry price and ATR that produced this signal, and
    Section 7.1 is otherwise the only place that data could survive past the
    LLM call. Documented in PROJECT.md alongside this change.
    """

    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    timeframe: Mapped[str] = mapped_column(String)
    candle_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    action: Mapped[Action] = mapped_column(String)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    reasoning: Mapped[str] = mapped_column(String(500))
    model_name: Mapped[str] = mapped_column(String)
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model_input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    atr_14: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    status: Mapped[SignalStatus] = mapped_column(String, default=SignalStatus.PENDING)
    # Positive-expectancy plan M2 (D3): promoted to first-class, queryable
    # columns rather than left inside `raw_response` — computed once per
    # cycle by `AnalysisPipeline` before the LLM call, so every signal has
    # them regardless of eventual action (D2/Section 1's gap with the older
    # `strategy_selected` raw_response field is exactly what this avoids).
    trade_score: Mapped[int | None] = mapped_column(index=True, nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    setup_regime: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    volatility_regime: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RiskDecision(Base):
    """PROJECT.md Section 7.2."""

    __tablename__ = "risk_decisions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    signal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("signals.id"), index=True)
    approved: Mapped[bool] = mapped_column()
    rejection_reason: Mapped[RejectionReason | None] = mapped_column(String, nullable=True)
    position_size_usdt: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    position_size_base: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_loss_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    equity_snapshot_usdt: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8), nullable=True
    )
    risk_pct_applied: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    # Positive-expectancy plan M1 (D1): `nominal_risk_amount_usdt` is the
    # pre-clamp equity*risk_pct budget; `actual_risk_usdt` is what was truly
    # at stake for this specific trade after sizing clamps
    # (`position_size_usdt * stop_distance_pct`) — the R-multiple
    # denominator. Both null when rejected (no sizing candidate reached).
    nominal_risk_amount_usdt: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    actual_risk_usdt: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_distance_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Base):
    """PROJECT.md Section 7.3."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    risk_decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("risk_decisions.id"), index=True)
    freqtrade_trade_id: Mapped[int | None] = mapped_column(nullable=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    side: Mapped[OrderSide] = mapped_column(String)
    status: Mapped[OrderStatus] = mapped_column(String, index=True)
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    filled_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    avg_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    dry_run: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Position(Base):
    """PROJECT.md Section 7.4."""

    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    symbol: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[PositionStatus] = mapped_column(String, index=True)
    entry_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    exit_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    pnl_usdt: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Positive-expectancy plan M1 — all nullable/set on close only, so
    # already-open positions from before this migration are unaffected
    # (D5: no retroactive backfill).
    exit_reason: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    fees_usdt: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    fees_estimated: Mapped[bool] = mapped_column(default=False)
    # `pnl_usdt / risk_decisions.actual_risk_usdt` via
    # `entry_order_id -> Order.risk_decision_id`; null for legacy rows
    # (predate this column) and for the rare case where the linked
    # RiskDecision has no `actual_risk_usdt` (e.g. pre-M1 decision).
    r_multiple: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    # Positive-expectancy plan M2 — denormalized from the entry `Signal` at
    # open (`admin_api/routers/webhooks.py::_handle_entry_fill`), same
    # precedent as `entry_price`/`amount` already being copied rather than
    # only living upstream (Section 3, M2). Null for positions opened
    # before this migration (D5: no retroactive backfill).
    market_regime: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    trade_score: Mapped[int | None] = mapped_column(index=True, nullable=True)
    # Positive-expectancy plan M4 — denormalized from the entry `Signal`
    # (`Signal.volatility_regime`, added on `signals` back in M2) at open,
    # same as `market_regime`/`trade_score` above. Powers the
    # expectancy-by-volatility breakdown on `GET /performance`. Null for
    # positions opened before migration `20260829_0001` (D5).
    volatility_regime: Mapped[str | None] = mapped_column(String, index=True, nullable=True)


class AuditEvent(Base):
    """PROJECT.md Section 7.5 — append-only."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    event_type: Mapped[AuditEventType] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PerformanceSnapshot(Base):
    """Positive-expectancy plan M3 — one row per scheduled Performance
    Engine recompute (`scheduler/app/jobs.py`), so expectancy degradation is
    visible as a time series rather than only a single live number (feeds
    M6 / vision-doc Phase 11's continuous-feedback loop).

    This is the *unfiltered* whole-account cohort; the live
    `GET /performance` endpoint recomputes on demand with symbol/regime/
    score filters and does not write here. Every metric column mirrors
    `common.performance.PerformanceReport`; the R/drawdown columns are
    nullable for the same reasons that dataclass's fields are Optional
    (legacy rows without `r_multiple`, no equity anchor for drawdown).
    """

    __tablename__ = "performance_snapshots"

    id: Mapped[uuid.UUID] = _uuid_pk()
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    trades: Mapped[int] = mapped_column()
    wins: Mapped[int] = mapped_column()
    losses: Mapped[int] = mapped_column()
    breakeven: Mapped[int] = mapped_column()
    trades_with_r: Mapped[int] = mapped_column()
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    avg_win_r: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    avg_loss_r: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    expectancy_r: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    total_r: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    total_pnl_usdt: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    profit_factor: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    max_drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    avg_drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    total_fees_usdt: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    # Always NULL for now — production has no per-trade slippage source
    # (implementation plan Section 1). Kept on the row so the column is
    # ready the day one exists, and so a snapshot mirrors the endpoint
    # response field-for-field.
    total_slippage_usdt: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    starting_equity_usdt: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)


class SystemState(Base):
    """PROJECT.md Section 7.6 — singleton row, id = 1."""

    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    killswitch_enabled: Mapped[bool] = mapped_column(default=False)
    killswitch_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    killswitch_updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    consecutive_loss_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskConfigState(Base):
    """PROJECT.md Section 7.7 — persisted overrides for `RiskConfig`
    (Section 9.1), applied on top of the env-sourced defaults so
    `PATCH /config` (Section 11) takes effect without a service restart.
    Singleton row, id = 1, same pattern as `SystemState`."""

    __tablename__ = "risk_config_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    overrides: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LLMConfigState(Base):
    """Persisted overrides for the LLM decision-engine levers (provider,
    model, temperature) — same pattern as `RiskConfigState`. `llm_service`
    itself stays off `core_net` (Section 3's Isolated Zone) and never reads
    this table directly; the Scheduler (which already has Postgres access
    and already calls `/analyze` every cycle) loads the effective config and
    forwards it as `AnalyzeRequest.provider_override`, so a config change
    takes effect on the next cycle without crossing the trust-zone boundary
    or restarting `llm_service`. Singleton row, id = 1, same pattern as
    `SystemState`."""

    __tablename__ = "llm_config_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    overrides: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NotifierState(Base):
    """PROJECT.md Section 7.8 — singleton cursor state for the Telegram
    Notifier (Section 6): how far into `audit_events` it has already
    notified, and the Telegram `getUpdates` offset for slash-command
    polling. Singleton row, id = 1.

    `(last_audit_created_at, last_audit_id)` is a full-precision cursor over
    `audit_events`, not just a timestamp — Postgres's `now()` is
    transaction-time, so multiple audit rows written in the same commit
    (e.g. `RISK_APPROVED` + `ORDER_SUBMITTED`) can share an identical
    `created_at`; pairing it with the row's UUID gives a total order with no
    new column needed on `audit_events`."""

    __tablename__ = "notifier_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    last_audit_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_audit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    last_telegram_update_id: Mapped[int | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
