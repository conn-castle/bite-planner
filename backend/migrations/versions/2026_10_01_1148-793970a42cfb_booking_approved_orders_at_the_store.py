"""Booking approved orders at the store

Revision ID: 793970a42cfb
Revises: fbd572b975bd
Create Date: 2026-10-01 11:48:41.946826

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "793970a42cfb"
down_revision: str | Sequence[str] | None = "fbd572b975bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOOK_STATES = (
    "waiting",
    "booking",
    "booked",
    "needs_time",
    "needs_product",
    "not_booked",
    "check_instacart",
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE job_kind ADD VALUE 'book'")
    book_state = postgresql.ENUM(*BOOK_STATES, name="book_state")
    book_state.create(op.get_bind())
    op.add_column("grocery_order", sa.Column("book_state", book_state, nullable=True))
    op.add_column("grocery_order", sa.Column("quote_id", sa.Uuid(), nullable=True))
    op.add_column("grocery_order", sa.Column("book_job_id", sa.Uuid(), nullable=True))
    op.add_column(
        "grocery_order", sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "grocery_order",
        sa.Column("booking", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "grocery_order",
        sa.Column("book_issue", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_foreign_key(
        "grocery_order_quote_id_fkey",
        "grocery_order",
        "job",
        ["quote_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "grocery_order_book_job_id_fkey",
        "grocery_order",
        "job",
        ["book_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Orders placed before booking existed went nowhere (the stub): they count as
    # booked, with nothing confirmed.
    op.execute("UPDATE grocery_order SET book_state = 'booked' WHERE status = 'placed'")
    op.create_check_constraint(
        "grocery_order_book_state_placed",
        "grocery_order",
        "(status = 'placed') = (book_state IS NOT NULL)",
    )
    op.create_check_constraint(
        "grocery_order_book_job_while_booking",
        "grocery_order",
        "(book_state = 'booking') = (book_job_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "grocery_order_approved_quote",
        "grocery_order",
        "status = 'draft' OR quote_id IS NOT NULL OR book_state = 'booked'",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("grocery_order_approved_quote", "grocery_order", type_="check")
    op.drop_constraint("grocery_order_book_job_while_booking", "grocery_order", type_="check")
    op.drop_constraint("grocery_order_book_state_placed", "grocery_order", type_="check")
    op.drop_constraint("grocery_order_book_job_id_fkey", "grocery_order", type_="foreignkey")
    op.drop_constraint("grocery_order_quote_id_fkey", "grocery_order", type_="foreignkey")
    op.drop_column("grocery_order", "book_issue")
    op.drop_column("grocery_order", "booking")
    op.drop_column("grocery_order", "clicked_at")
    op.drop_column("grocery_order", "book_job_id")
    op.drop_column("grocery_order", "quote_id")
    op.drop_column("grocery_order", "book_state")
    op.execute("DROP TYPE book_state")
    # Postgres can't drop an enum value: rebuild the type without it.
    op.execute("DELETE FROM job WHERE kind = 'book'")
    op.execute("ALTER TYPE job_kind RENAME TO job_kind_old")
    op.execute("CREATE TYPE job_kind AS ENUM ('generate_meals', 'build_list', 'shop', 'quote')")
    op.execute("ALTER TABLE job ALTER COLUMN kind TYPE job_kind USING kind::text::job_kind")
    op.execute("DROP TYPE job_kind_old")
