"""Positive-expectancy plan M1: persist R as a first-class unit.

Adds `nominal_risk_amount_usdt`, `actual_risk_usdt`, `stop_distance_pct` to
`risk_decisions`, and `exit_reason`, `fees_usdt`, `fees_estimated`,
`r_multiple` to `positions`. All columns nullable and additive-only — zero
behavior change (docs/trademind_positive_expectancy_implementation_plan.md
Section 3, M1). `fees_estimated` is non-nullable with a `false` server
default so existing rows and new inserts alike have a concrete value.

Revision ID: 20260815_0001
Revises: 20260729_0001
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0001"
down_revision: str | None = "20260729_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "risk_decisions",
        sa.Column("nominal_risk_amount_usdt", sa.Numeric(20, 8), nullable=True),
    )
    op.add_column(
        "risk_decisions",
        sa.Column("actual_risk_usdt", sa.Numeric(20, 8), nullable=True),
    )
    op.add_column(
        "risk_decisions",
        sa.Column("stop_distance_pct", sa.Numeric(10, 6), nullable=True),
    )

    op.add_column("positions", sa.Column("exit_reason", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_positions_exit_reason"), "positions", ["exit_reason"], unique=False
    )
    op.add_column("positions", sa.Column("fees_usdt", sa.Numeric(20, 8), nullable=True))
    op.add_column(
        "positions",
        sa.Column(
            "fees_estimated", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("positions", sa.Column("r_multiple", sa.Numeric(10, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("positions", "r_multiple")
    op.drop_column("positions", "fees_estimated")
    op.drop_column("positions", "fees_usdt")
    op.drop_index(op.f("ix_positions_exit_reason"), table_name="positions")
    op.drop_column("positions", "exit_reason")

    op.drop_column("risk_decisions", "stop_distance_pct")
    op.drop_column("risk_decisions", "actual_risk_usdt")
    op.drop_column("risk_decisions", "nominal_risk_amount_usdt")
