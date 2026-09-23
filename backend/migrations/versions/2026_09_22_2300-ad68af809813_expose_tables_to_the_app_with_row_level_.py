"""expose tables to the app with row level security

The app reaches these tables through the Neon Data API as the `authenticated`
role. Everyone in the household sees all meals and jobs; the app can only queue
meal generation jobs. The worker uses the owner role, which bypasses RLS.

Revision ID: ad68af809813
Revises: 6e34beab8c6d
Create Date: 2026-09-22 23:00:53.578563

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ad68af809813"
down_revision: str | Sequence[str] | None = "6e34beab8c6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = ["meal", "meal_ingredient", "job", "job_meal"]


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("GRANT USAGE ON SCHEMA public TO authenticated")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"GRANT SELECT ON {table} TO authenticated")
        op.execute(
            f"CREATE POLICY household_read ON {table} FOR SELECT TO authenticated USING (true)"
        )
    # Only kind and params are writable; status and requested_by use their defaults.
    op.execute("GRANT INSERT (kind, params) ON job TO authenticated")
    op.execute(
        "CREATE POLICY request_meals ON job FOR INSERT TO authenticated "
        "WITH CHECK (kind = 'generate_meals' AND requested_by = (SELECT auth.uid()))"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP POLICY request_meals ON job")
    op.execute("REVOKE INSERT (kind, params) ON job FROM authenticated")
    for table in TABLES:
        op.execute(f"DROP POLICY household_read ON {table}")
        op.execute(f"REVOKE SELECT ON {table} FROM authenticated")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE USAGE ON SCHEMA public FROM authenticated")
