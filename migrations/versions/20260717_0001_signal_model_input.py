"""signal_model_input: persist the exact LLM input payload per signal

Revision ID: 20260717_0001
Revises: 20260716_0001
Create Date: 2026-07-17

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260717_0001"
down_revision: Union[str, None] = "20260716_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("model_input", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("signals", "model_input")
