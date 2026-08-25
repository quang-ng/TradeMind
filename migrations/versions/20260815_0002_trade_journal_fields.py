"""Positive-expectancy plan M2: promote regime to a queryable column, add
Trade Score, propagate both onto `Position` at close.

Adds `trade_score`, `score_breakdown`, `setup_regime`, `volatility_regime`
to `signals`, and `market_regime`, `trade_score` to `positions`. All columns
nullable and additive-only — zero behavior change
(docs/trademind_positive_expectancy_implementation_plan.md Section 3, M2).

Revision ID: 20260815_0002
Revises: 20260815_0001
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0002"
down_revision: str | None = "20260815_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("trade_score", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_signals_trade_score"), "signals", ["trade_score"], unique=False)
    op.add_column(
        "signals", sa.Column("score_breakdown", postgresql.JSONB(), nullable=True)
    )
    op.add_column("signals", sa.Column("setup_regime", sa.String(), nullable=True))
    op.create_index(op.f("ix_signals_setup_regime"), "signals", ["setup_regime"], unique=False)
    op.add_column("signals", sa.Column("volatility_regime", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_signals_volatility_regime"), "signals", ["volatility_regime"], unique=False
    )

    op.add_column("positions", sa.Column("market_regime", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_positions_market_regime"), "positions", ["market_regime"], unique=False
    )
    op.add_column("positions", sa.Column("trade_score", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_positions_trade_score"), "positions", ["trade_score"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_positions_trade_score"), table_name="positions")
    op.drop_column("positions", "trade_score")
    op.drop_index(op.f("ix_positions_market_regime"), table_name="positions")
    op.drop_column("positions", "market_regime")

    op.drop_index(op.f("ix_signals_volatility_regime"), table_name="signals")
    op.drop_column("signals", "volatility_regime")
    op.drop_index(op.f("ix_signals_setup_regime"), table_name="signals")
    op.drop_column("signals", "setup_regime")
    op.drop_column("signals", "score_breakdown")
    op.drop_index(op.f("ix_signals_trade_score"), table_name="signals")
    op.drop_column("signals", "trade_score")
