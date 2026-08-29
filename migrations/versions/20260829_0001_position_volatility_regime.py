"""Positive-expectancy plan M4: denormalize `volatility_regime` onto `positions`.

Adds one nullable, indexed column — `positions.volatility_regime` — copied
from the entry `Signal.volatility_regime` (added to `signals` in M2) at open,
exactly as `market_regime`/`trade_score` already are. Powers the
expectancy-by-volatility breakdown on `GET /performance`
(docs/trademind_positive_expectancy_implementation_plan.md Section 4, M4).

Additive-only, nullable, no backfill — zero behavior change, safe to ship to
the live system; existing/open positions stay `NULL` (D5).

Revision ID: 20260829_0001
Revises: 20260827_0001
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0001"
down_revision: str | None = "20260827_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("volatility_regime", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_positions_volatility_regime"),
        "positions",
        ["volatility_regime"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_positions_volatility_regime"), table_name="positions")
    op.drop_column("positions", "volatility_regime")
