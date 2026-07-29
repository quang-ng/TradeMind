"""Persist the operator acknowledgement boundary for consecutive losses.

Revision ID: 20260729_0001
Revises: 20260727_0001
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_0001"
down_revision: str | None = "20260727_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "system_state",
        sa.Column("consecutive_loss_reset_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("system_state", "consecutive_loss_reset_at")
