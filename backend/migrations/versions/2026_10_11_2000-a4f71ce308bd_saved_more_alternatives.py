"""Save a generated broad query and its prefetched alternatives on each list item.

Revision ID: a4f71ce308bd
Revises: 58ef26192c74
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4f71ce308bd"
down_revision: str | Sequence[str] | None = "58ef26192c74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "shopping_list_item", sa.Column("more_alternatives_query", sa.String(), nullable=True)
    )
    op.add_column(
        "shopping_list_item",
        sa.Column(
            "more_alternatives",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("shopping_list_item", "more_alternatives")
    op.drop_column("shopping_list_item", "more_alternatives_query")
