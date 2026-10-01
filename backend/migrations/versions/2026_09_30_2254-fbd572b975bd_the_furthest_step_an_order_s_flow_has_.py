"""The furthest step an order's flow has reached

Revision ID: fbd572b975bd
Revises: 108b9b6ad4fc
Create Date: 2026-09-30 22:54:07.457117

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "fbd572b975bd"
down_revision: str | Sequence[str] | None = "108b9b6ad4fc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "grocery_order",
        sa.Column(
            "reached_step",
            # The lock's type, already there.
            postgresql.ENUM(name="order_step", create_type=False),
            server_default="pick",
            nullable=False,
        ),
    )
    # A draft open now has got at least as far as its lock's step.
    op.execute("UPDATE grocery_order SET reached_step = lock_step WHERE lock_step IS NOT NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("grocery_order", "reached_step")
