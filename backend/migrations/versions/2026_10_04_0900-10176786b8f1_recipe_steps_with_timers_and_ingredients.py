"""Recipe steps with timers and the ingredients they use

Revision ID: 10176786b8f1
Revises: 1bab82dadb82
Create Date: 2026-10-04 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "10176786b8f1"
down_revision: str | Sequence[str] | None = "1bab82dadb82"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # A step was its text; a meal from before has no timers or ingredients for its
    # steps. Steps already in the new shape are left as they are.
    op.execute(
        """
        UPDATE meal SET steps = coalesce(
            (SELECT jsonb_agg(
                CASE jsonb_typeof(step)
                    WHEN 'string' THEN jsonb_build_object(
                        'text', step #>> '{}', 'timer_minutes', null, 'ingredients', '[]'::jsonb)
                    ELSE step
                END
                ORDER BY position)
             FROM jsonb_array_elements(meal.steps) WITH ORDINALITY AS s(step, position)),
            '[]'::jsonb)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
        UPDATE meal SET steps = coalesce(
            (SELECT jsonb_agg(
                CASE jsonb_typeof(step) WHEN 'object' THEN step->'text' ELSE step END
                ORDER BY position)
             FROM jsonb_array_elements(meal.steps) WITH ORDINALITY AS s(step, position)),
            '[]'::jsonb)
        """
    )
