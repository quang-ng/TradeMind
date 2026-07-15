"""initial schema: signals, risk_decisions, orders, positions, audit_events, system_state

Revision ID: 20260715_0001
Revises:
Create Date: 2026-07-15

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260715_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_column(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _uuid_fk_column(name: str, target: str, *, nullable: bool) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), sa.ForeignKey(target), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("timeframe", sa.String(), nullable=False),
        sa.Column("candle_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column("reasoning", sa.String(500), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("raw_response", postgresql.JSONB(), nullable=True),
        sa.Column("price", sa.Numeric(20, 8), nullable=False),
        sa.Column("atr_14", sa.Numeric(20, 8), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        _timestamp_column("created_at"),
    )
    op.create_index("ix_signals_trace_id", "signals", ["trace_id"])
    op.create_index("ix_signals_symbol", "signals", ["symbol"])

    op.create_table(
        "risk_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid_fk_column("signal_id", "signals.id", nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("rejection_reason", sa.String(), nullable=True),
        sa.Column("position_size_usdt", sa.Numeric(20, 8), nullable=True),
        sa.Column("position_size_base", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop_loss_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("equity_snapshot_usdt", sa.Numeric(20, 8), nullable=False),
        sa.Column("risk_pct_applied", sa.Numeric(6, 4), nullable=True),
        _timestamp_column("created_at"),
    )
    op.create_index("ix_risk_decisions_trace_id", "risk_decisions", ["trace_id"])
    op.create_index("ix_risk_decisions_signal_id", "risk_decisions", ["signal_id"])

    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        _uuid_fk_column("risk_decision_id", "risk_decisions.id", nullable=False),
        sa.Column("freqtrade_trade_id", sa.Integer(), nullable=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("requested_amount", sa.Numeric(20, 8), nullable=False),
        sa.Column("filled_amount", sa.Numeric(20, 8), nullable=True),
        sa.Column("avg_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        _timestamp_column("created_at"),
        _timestamp_column("updated_at"),
    )
    op.create_index("ix_orders_trace_id", "orders", ["trace_id"])
    op.create_index("ix_orders_risk_decision_id", "orders", ["risk_decision_id"])
    op.create_index("ix_orders_symbol", "orders", ["symbol"])
    op.create_index("ix_orders_status", "orders", ["status"])

    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        _uuid_fk_column("entry_order_id", "orders.id", nullable=False),
        _uuid_fk_column("exit_order_id", "orders.id", nullable=True),
        sa.Column("entry_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("exit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("amount", sa.Numeric(20, 8), nullable=False),
        sa.Column("pnl_usdt", sa.Numeric(20, 8), nullable=True),
        sa.Column("pnl_pct", sa.Numeric(10, 4), nullable=True),
        _timestamp_column("opened_at"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_positions_symbol", "positions", ["symbol"])
    op.create_index("ix_positions_status", "positions", ["status"])

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _timestamp_column("created_at"),
    )
    op.create_index("ix_audit_events_trace_id", "audit_events", ["trace_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])

    op.create_table(
        "system_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("killswitch_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("killswitch_reason", sa.String(), nullable=True),
        sa.Column("killswitch_updated_by", sa.String(), nullable=True),
        _timestamp_column("updated_at"),
    )
    op.execute("INSERT INTO system_state (id, killswitch_enabled) VALUES (1, false)")


def downgrade() -> None:
    op.drop_table("system_state")
    op.drop_table("audit_events")
    op.drop_table("positions")
    op.drop_table("orders")
    op.drop_table("risk_decisions")
    op.drop_table("signals")
