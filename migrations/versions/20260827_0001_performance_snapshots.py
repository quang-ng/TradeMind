"""Positive-expectancy plan M3: performance-metrics snapshot history.

Creates `performance_snapshots` — one row per scheduled Performance Engine
recompute (`scheduler/app/jobs.py`), a timestamped time series of the
whole-account R metrics so expectancy degradation is visible over time
(docs/trademind_positive_expectancy_implementation_plan.md Section 4, M3).

New table only, no change to any existing row or column — zero behavior
change, safe to ship to the live system.

Revision ID: 20260827_0001
Revises: 20260815_0002
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260827_0001"
down_revision: str | None = "20260815_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "performance_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("trades", sa.Integer(), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False),
        sa.Column("losses", sa.Integer(), nullable=False),
        sa.Column("breakeven", sa.Integer(), nullable=False),
        sa.Column("trades_with_r", sa.Integer(), nullable=False),
        sa.Column("win_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("avg_win_r", sa.Numeric(10, 4), nullable=True),
        sa.Column("avg_loss_r", sa.Numeric(10, 4), nullable=True),
        sa.Column("expectancy_r", sa.Numeric(10, 4), nullable=True),
        sa.Column("total_r", sa.Numeric(14, 4), nullable=True),
        sa.Column("total_pnl_usdt", sa.Numeric(20, 8), nullable=False),
        sa.Column("profit_factor", sa.Numeric(12, 4), nullable=True),
        sa.Column("max_drawdown_pct", sa.Numeric(10, 6), nullable=True),
        sa.Column("avg_drawdown_pct", sa.Numeric(10, 6), nullable=True),
        sa.Column("total_fees_usdt", sa.Numeric(20, 8), nullable=False),
        sa.Column("total_slippage_usdt", sa.Numeric(20, 8), nullable=True),
        sa.Column("starting_equity_usdt", sa.Numeric(20, 8), nullable=True),
    )
    op.create_index(
        op.f("ix_performance_snapshots_computed_at"),
        "performance_snapshots",
        ["computed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_performance_snapshots_computed_at"),
        table_name="performance_snapshots",
    )
    op.drop_table("performance_snapshots")
