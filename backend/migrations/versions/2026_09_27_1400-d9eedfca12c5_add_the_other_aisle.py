"""Add the other aisle, for shopping list items no other aisle fits

Revision ID: d9eedfca12c5
Revises: 744721360e3b
Create Date: 2026-09-27 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9eedfca12c5"
down_revision: str | Sequence[str] | None = "744721360e3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE aisle ADD VALUE 'other'")


def downgrade() -> None:
    """Downgrade schema."""
    # Postgres can't drop an enum value: rebuild the type without it, with its
    # items in the pantry.
    op.execute("UPDATE shopping_list_item SET aisle = 'pantry' WHERE aisle = 'other'")
    op.execute("ALTER TYPE aisle RENAME TO aisle_old")
    sa.Enum("produce", "meat_seafood", "dairy_eggs", "frozen", "pantry", name="aisle").create(
        op.get_bind()
    )
    op.execute(
        "ALTER TABLE shopping_list_item ALTER COLUMN aisle TYPE aisle USING aisle::text::aisle"
    )
    op.execute("DROP TYPE aisle_old")
