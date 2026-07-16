"""llm_config_state: persisted overrides for LLM provider/model/temperature

Revision ID: 20260716_0001
Revises: 20260715_0002
Create Date: 2026-07-16

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260716_0001"
down_revision: Union[str, None] = "20260715_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_column(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "llm_config_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("overrides", postgresql.JSONB(), nullable=True),
        _timestamp_column("updated_at"),
    )
    op.execute("INSERT INTO llm_config_state (id, overrides) VALUES (1, NULL)")


def downgrade() -> None:
    op.drop_table("llm_config_state")
