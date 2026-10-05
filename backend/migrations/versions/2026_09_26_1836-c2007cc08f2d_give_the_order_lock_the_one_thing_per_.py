"""Give the order lock the one-thing-per-step flow's steps

Revision ID: c2007cc08f2d
Revises: c887e4796475
Create Date: 2026-09-26 18:36:15.288635

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2007cc08f2d"
down_revision: str | Sequence[str] | None = "c887e4796475"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_STEPS = ("pick", "dates")
NEW_STEPS = ("pick", "servings", "delivery", "next", "review")


def _retype(steps: tuple[str, ...], mapping: str) -> None:
    """Rebuild the order_step type with these values, mapping the column's old
    ones by `mapping` (a CASE expression over lock_step)."""
    # contract: pre-release (no real users yet); the previous release wrote lock_step 'dates'
    op.execute("ALTER TYPE order_step RENAME TO order_step_old")
    sa.Enum(*steps, name="order_step").create(op.get_bind())
    op.execute(
        "ALTER TABLE grocery_order ALTER COLUMN lock_step TYPE order_step "
        f"USING ({mapping})::order_step"
    )
    op.execute("DROP TYPE order_step_old")


def upgrade() -> None:
    """Upgrade schema."""
    # A lock held at "dates" (dates and servings) is now at the delivery date.
    _retype(NEW_STEPS, "CASE lock_step::text WHEN 'dates' THEN 'delivery' ELSE lock_step::text END")


def downgrade() -> None:
    """Downgrade schema."""
    # Unlocked orders (no step) stay without one.
    _retype(
        OLD_STEPS,
        "CASE WHEN lock_step IS NULL THEN NULL WHEN lock_step::text = 'pick' THEN 'pick' "
        "ELSE 'dates' END",
    )
