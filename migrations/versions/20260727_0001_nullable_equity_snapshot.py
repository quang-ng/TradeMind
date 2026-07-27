"""Allow dependency-failure decisions without a fabricated equity value.

Revision ID: 20260727_0001
Revises: 20260717_0001
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0001"
down_revision: str | None = "20260717_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "risk_decisions",
        "equity_snapshot_usdt",
        existing_type=sa.Numeric(20, 8),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE risk_decisions
        SET equity_snapshot_usdt = 0
        WHERE equity_snapshot_usdt IS NULL
        """
    )
    op.alter_column(
        "risk_decisions",
        "equity_snapshot_usdt",
        existing_type=sa.Numeric(20, 8),
        nullable=False,
    )
