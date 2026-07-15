"""phase 4: risk_config_state, notifier_state

Revision ID: 20260715_0002
Revises: 20260715_0001
Create Date: 2026-07-15

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260715_0002"
down_revision: Union[str, None] = "20260715_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_column(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "risk_config_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("overrides", postgresql.JSONB(), nullable=True),
        _timestamp_column("updated_at"),
    )
    op.execute("INSERT INTO risk_config_state (id, overrides) VALUES (1, NULL)")

    op.create_table(
        "notifier_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_audit_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_telegram_update_id", sa.BigInteger(), nullable=True),
        _timestamp_column("updated_at"),
    )
    # Fresh notifier starts from "now" — it must never replay a backlog of
    # pre-existing audit history into Telegram on first boot (PROJECT.md
    # Section 7.8).
    op.execute(
        "INSERT INTO notifier_state (id, last_audit_created_at, last_audit_id) "
        "VALUES (1, now(), '00000000-0000-0000-0000-000000000000')"
    )


def downgrade() -> None:
    op.drop_table("notifier_state")
    op.drop_table("risk_config_state")
