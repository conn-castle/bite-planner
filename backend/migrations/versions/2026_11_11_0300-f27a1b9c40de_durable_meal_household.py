"""Retain meal household ownership after draft discard.

Revision ID: f27a1b9c40de
Revises: e7f3c9012a64
"""

import sqlalchemy as sa
from alembic import op

revision: str = "f27a1b9c40de"
down_revision: str | None = "e7f3c9012a64"
branch_labels: str | None = None
depends_on: str | None = None

# Every surviving source must agree, including ratings on meals with a job.
_OWNERS: str = """
WITH owners AS (
    SELECT meal.id AS meal_id, grocery_order.household_id
    FROM meal JOIN job ON job.id = meal.job_id
    JOIN grocery_order ON grocery_order.id = job.grocery_order_id
    UNION
    SELECT meal_id, household_id FROM meal_preference
), resolved AS (
    SELECT meal.id, count(DISTINCT owners.household_id) AS owner_count,
           min(owners.household_id::text)::uuid AS household_id
    FROM meal LEFT JOIN owners ON owners.meal_id = meal.id GROUP BY meal.id
)
"""


def upgrade() -> None:
    op.add_column("meal", sa.Column("household_id", sa.Uuid(), nullable=True))
    connection = op.get_bind()
    invalid = connection.execute(
        sa.text(_OWNERS + "SELECT id, owner_count FROM resolved WHERE owner_count <> 1 ORDER BY id")
    ).all()
    if invalid:
        raise RuntimeError(
            f"Cannot backfill meal household ownership: {len(invalid)} unknown/conflicting meals "
            f"(meal_id, distinct owners): {invalid}. Resolve ownership from authoritative "
            "job/order or rating evidence before retrying; no ownership was guessed."
        )
    connection.execute(
        sa.text(
            _OWNERS + "UPDATE meal SET household_id = resolved.household_id "
            "FROM resolved WHERE meal.id = resolved.id"
        )
    )
    # Drain old jobs before applying this migration.
    # contract: old workers omit household_id; code-only rollback is incompatible.
    op.alter_column("meal", "household_id", nullable=False)
    op.create_foreign_key("meal_household_id_fkey", "meal", "household", ["household_id"], ["id"])
    op.create_index("ix_meal_household_id", "meal", ["household_id"])


def downgrade() -> None:
    op.drop_index("ix_meal_household_id", table_name="meal")
    op.drop_constraint("meal_household_id_fkey", "meal", type_="foreignkey")
    op.drop_column("meal", "household_id")
