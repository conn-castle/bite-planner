"""Shop jobs, and the products chosen for shopping list items

Revision ID: b1993bea9e33
Revises: d9eedfca12c5
Create Date: 2026-09-28 08:35:06.488033

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b1993bea9e33"
down_revision: str | Sequence[str] | None = "d9eedfca12c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE job_kind ADD VALUE 'shop'")
    op.add_column("job", sa.Column("build_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_job_build_id"), "job", ["build_id"], unique=False)
    op.create_foreign_key(
        "job_build_id_fkey", "job", "job", ["build_id"], ["id"], ondelete="CASCADE"
    )
    op.create_check_constraint(
        "job_build_for_shops", "job", "(kind::text = 'shop') = (build_id IS NOT NULL)"
    )
    op.add_column(
        "shopping_list_item",
        sa.Column("product", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "shopping_list_item",
        sa.Column(
            "alternatives",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "shopping_list_item", sa.Column("no_alternatives_reason", sa.String(), nullable=True)
    )
    op.add_column(
        "shopping_list_item",
        sa.Column("not_found", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("shopping_list_item", sa.Column("skippable_reason", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("shopping_list_item", "skippable_reason")
    op.drop_column("shopping_list_item", "not_found")
    op.drop_column("shopping_list_item", "no_alternatives_reason")
    op.drop_column("shopping_list_item", "alternatives")
    op.drop_column("shopping_list_item", "product")
    op.drop_constraint("job_build_for_shops", "job", type_="check")
    op.drop_constraint("job_build_id_fkey", "job", type_="foreignkey")
    op.drop_index(op.f("ix_job_build_id"), table_name="job")
    op.drop_column("job", "build_id")
    # Postgres can't drop an enum value: rebuild the type without it (its jobs go
    # with the column above).
    op.execute("DELETE FROM job WHERE kind = 'shop'")
    op.execute("ALTER TYPE job_kind RENAME TO job_kind_old")
    sa.Enum("generate_meals", "build_list", name="job_kind").create(op.get_bind())
    op.execute("ALTER TABLE job ALTER COLUMN kind TYPE job_kind USING kind::text::job_kind")
    op.execute("DROP TYPE job_kind_old")
