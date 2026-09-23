"""harden meal requests

- The params check rejected wrong counts but accepted a missing one (a NULL
  CHECK result passes); it now requires the count.
- Two simultaneous requests could both see 2 active jobs and exceed the limit
  of 3; `active_job_count()` now serializes each person's requests with an
  advisory lock. It is VOLATILE so its count sees rows committed while it waited.

Revision ID: e44dea213893
Revises: 13fe1db3aaf8
Create Date: 2026-09-23 11:32:44.751872

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e44dea213893"
down_revision: str | Sequence[str] | None = "13fe1db3aaf8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PARAMS = (
    "jsonb_typeof(params->'count') = 'number' "
    "AND (params->>'count')::numeric BETWEEN 1 AND 14 "
    "AND (params->>'count')::numeric = trunc((params->>'count')::numeric) "
    "AND params - 'count' = '{}'::jsonb"
)
NEW_CHECK = f"kind <> 'generate_meals' OR coalesce({PARAMS}, false)"
OLD_CHECK = f"kind <> 'generate_meals' OR ({PARAMS})"

COUNT_BODY = """
  SELECT count(*) FROM public.job
  WHERE requested_by = auth.uid() AND status IN ('pending', 'running')
"""


def replace_count_function(volatility: str, body: str) -> None:
    op.execute(
        "CREATE OR REPLACE FUNCTION app_private.active_job_count() RETURNS bigint "
        f"LANGUAGE sql {volatility} SECURITY DEFINER SET search_path = '' AS $$ {body} $$"
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("job_generate_meals_params", "job", type_="check")
    op.create_check_constraint("job_generate_meals_params", "job", NEW_CHECK)
    replace_count_function(
        "VOLATILE",
        "SELECT pg_advisory_xact_lock(hashtextextended(auth.uid()::text, 0));" + COUNT_BODY,
    )


def downgrade() -> None:
    """Downgrade schema."""
    replace_count_function("STABLE", COUNT_BODY)
    op.drop_constraint("job_generate_meals_params", "job", type_="check")
    op.create_check_constraint("job_generate_meals_params", "job", OLD_CHECK)
