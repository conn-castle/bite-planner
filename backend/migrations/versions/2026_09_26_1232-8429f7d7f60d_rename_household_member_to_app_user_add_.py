"""Rename household_member to app_user (the app's side of a user); add servings to picks.

Revision ID: 8429f7d7f60d
Revises: 5141465ee516
Create Date: 2026-09-26 12:32:14.129072

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8429f7d7f60d"
down_revision: str | Sequence[str] | None = "5141465ee516"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # The app's side of a user: renamed, with its constraint and index names.
    # Pre-release (no users): the previous release's API reads household_member
    # until this release's deploy replaces it, minutes later.
    # contract: pre-release; the previous API breaks only until this deploy.
    op.rename_table("household_member", "app_user")
    # contract: the constraint and index names follow the table's.
    op.execute("ALTER TABLE app_user RENAME CONSTRAINT household_member_pkey TO app_user_pkey")
    # contract: as above.
    op.execute(
        "ALTER TABLE app_user RENAME CONSTRAINT household_member_household_id_fkey "
        "TO app_user_household_id_fkey"
    )
    # contract: as above.
    op.execute("ALTER INDEX ix_household_member_household_id RENAME TO ix_app_user_household_id")

    # A pick's servings: existing picks get their household's default, and every
    # new pick sets its own (so no default stays on the column).
    op.add_column("grocery_order_meal", sa.Column("servings", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE grocery_order_meal AS p SET servings = h.default_servings "
        "FROM grocery_order AS o JOIN household AS h ON h.id = o.household_id "
        "WHERE o.id = p.grocery_order_id"
    )
    op.alter_column("grocery_order_meal", "servings", nullable=False)
    op.create_check_constraint(
        "grocery_order_meal_servings_range", "grocery_order_meal", "servings BETWEEN 1 AND 12"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Dropping the column drops its check constraint.
    op.drop_column("grocery_order_meal", "servings")
    op.execute("ALTER INDEX ix_app_user_household_id RENAME TO ix_household_member_household_id")
    op.execute(
        "ALTER TABLE app_user RENAME CONSTRAINT app_user_household_id_fkey "
        "TO household_member_household_id_fkey"
    )
    op.execute("ALTER TABLE app_user RENAME CONSTRAINT app_user_pkey TO household_member_pkey")
    op.rename_table("app_user", "household_member")
