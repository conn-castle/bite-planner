"""Remember the validated delivery destination and the destination used for shopping.

Revision ID: 58ef26192c74
Revises: b126aa72c0bd
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "58ef26192c74"
down_revision: str | Sequence[str] | None = "b126aa72c0bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("household", sa.Column("delivery_address", postgresql.JSONB(), nullable=True))
    op.add_column("job", sa.Column("delivery_address", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("job", "delivery_address")
    op.drop_column("household", "delivery_address")
