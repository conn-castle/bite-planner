"""Quote jobs, delivery times, and the household's tip and cart settings

Revision ID: 108b9b6ad4fc
Revises: b1993bea9e33
Create Date: 2026-09-28 22:46:48.076618

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "108b9b6ad4fc"
down_revision: str | Sequence[str] | None = "b1993bea9e33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE job_kind ADD VALUE 'quote'")
    op.add_column("grocery_order", sa.Column("delivery_by", sa.Time(), nullable=True))
    op.add_column(
        "household", sa.Column("delivery_time", sa.Time(), server_default="18:00", nullable=False)
    )
    op.add_column(
        "household", sa.Column("tip_percent", sa.Integer(), server_default="10", nullable=False)
    )
    op.add_column(
        "household", sa.Column("tip_max_cents", sa.Integer(), server_default="1500", nullable=False)
    )
    product_gone = postgresql.ENUM("use_alternative", "ask", name="product_gone")
    product_gone.create(op.get_bind())
    op.add_column(
        "household",
        sa.Column("product_gone", product_gone, server_default="use_alternative", nullable=False),
    )
    op.add_column(
        "household",
        sa.Column("cart_ledger", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        "household_tip_percent_range", "household", "tip_percent BETWEEN 0 AND 30"
    )
    op.create_check_constraint(
        "household_tip_max_cents_range", "household", "tip_max_cents BETWEEN 0 AND 10000"
    )
    op.add_column(
        "job", sa.Column("quote_request", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("job", sa.Column("quote", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_check_constraint(
        "job_quote_request_for_quotes",
        "job",
        "(kind::text = 'quote') = (quote_request IS NOT NULL)",
    )
    op.create_check_constraint(
        "job_quote_only_quotes", "job", "kind::text = 'quote' OR quote IS NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("job_quote_only_quotes", "job", type_="check")
    op.drop_constraint("job_quote_request_for_quotes", "job", type_="check")
    op.drop_column("job", "quote")
    op.drop_column("job", "quote_request")
    op.drop_constraint("household_tip_max_cents_range", "household", type_="check")
    op.drop_constraint("household_tip_percent_range", "household", type_="check")
    op.drop_column("household", "cart_ledger")
    op.drop_column("household", "product_gone")
    op.execute("DROP TYPE product_gone")
    op.drop_column("household", "tip_max_cents")
    op.drop_column("household", "tip_percent")
    op.drop_column("household", "delivery_time")
    op.drop_column("grocery_order", "delivery_by")
    # Postgres can't drop an enum value: rebuild the type without it.
    op.execute("DELETE FROM job WHERE kind = 'quote'")
    op.execute("ALTER TYPE job_kind RENAME TO job_kind_old")
    sa.Enum("generate_meals", "build_list", "shop", name="job_kind").create(op.get_bind())
    op.execute("ALTER TABLE job ALTER COLUMN kind TYPE job_kind USING kind::text::job_kind")
    op.execute("DROP TYPE job_kind_old")
