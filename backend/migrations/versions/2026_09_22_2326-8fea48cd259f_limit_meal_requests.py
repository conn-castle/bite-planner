"""limit meal requests

Enforces the shape of app-inserted `generate_meals` params and caps each member
at 3 pending or running jobs.

Revision ID: 8fea48cd259f
Revises: ad68af809813
Create Date: 2026-09-22 23:26:59.002544

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8fea48cd259f"
down_revision: str | Sequence[str] | None = "ad68af809813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PARAMS_CHECK = (
    "kind <> 'generate_meals' OR ("
    "jsonb_typeof(params->'count') = 'number' "
    "AND (params->>'count')::numeric BETWEEN 1 AND 14 "
    "AND (params->>'count')::numeric = trunc((params->>'count')::numeric) "
    "AND params - 'count' = '{}'::jsonb)"
)
REQUEST_CHECK = "kind = 'generate_meals' AND requested_by = (SELECT auth.uid())"
ACTIVE_JOB_LIMIT = (
    "(SELECT count(*) FROM job AS active WHERE active.requested_by = (SELECT auth.uid()) "
    "AND active.status IN ('pending', 'running')) < 3"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint("job_generate_meals_params", "job", PARAMS_CHECK)
    op.execute("DROP POLICY request_meals ON job")
    op.execute(
        "CREATE POLICY request_meals ON job FOR INSERT TO authenticated "
        f"WITH CHECK ({REQUEST_CHECK} AND {ACTIVE_JOB_LIMIT})"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP POLICY request_meals ON job")
    op.execute(
        "CREATE POLICY request_meals ON job FOR INSERT TO authenticated "
        f"WITH CHECK ({REQUEST_CHECK})"
    )
    op.drop_constraint("job_generate_meals_params", "job", type_="check")
