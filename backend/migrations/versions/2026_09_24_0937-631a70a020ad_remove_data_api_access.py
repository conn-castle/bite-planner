"""remove Data API access

Since r6 the app goes through the API, which connects as the owner role, so the
Data API's grants, row-level security, policies, and helper functions, and the
`client_log_batch` table (app logs now go to CloudWatch), are unused. Also drops
the `auth.uid()` column defaults, so nothing depends on the Data API's `auth`
schema once it is disabled.

The downgrade needs `auth.uid()`, which Neon Auth provides (it stays after the
Data API is deleted); on a branch without it, restore a snapshot instead.

Revision ID: 631a70a020ad
Revises: e44dea213893
Create Date: 2026-09-24 09:37:37.411162

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "631a70a020ad"
down_revision: str | Sequence[str] | None = "e44dea213893"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOUSEHOLD_TABLES = ["meal", "meal_ingredient", "job", "job_meal"]
POLICIES = [(table, "household_read") for table in HOUSEHOLD_TABLES] + [
    ("job", "request_meals"),
    ("household_member", "own_membership"),
]
EXPOSED_TABLES = [*HOUSEHOLD_TABLES, "household_member"]


def upgrade() -> None:
    """Upgrade schema."""
    # contract: app logs go to the API (CloudWatch) since r6; nothing reads this table
    op.drop_table("client_log_batch")
    for table, policy in POLICIES:
        op.execute(f"DROP POLICY {policy} ON {table}")
    for table in EXPOSED_TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM authenticated")
    op.execute("REVOKE USAGE ON SCHEMA public FROM authenticated")
    op.execute("DROP SCHEMA app_private CASCADE")  # the policies' helper functions
    # The API sets requested_by itself.
    op.alter_column("job", "requested_by", server_default=None)


def downgrade() -> None:
    """Downgrade schema: the Data API access as of e44dea213893."""
    op.alter_column("job", "requested_by", server_default=sa.text("auth.uid()"))
    op.execute(
        """
        CREATE SCHEMA app_private;
        CREATE FUNCTION app_private.is_household_member() RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
          SELECT EXISTS (SELECT 1 FROM public.household_member WHERE user_id = auth.uid())
        $$;
        CREATE FUNCTION app_private.active_job_count() RETURNS bigint
        LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
          SELECT pg_advisory_xact_lock(hashtextextended(auth.uid()::text, 0));
          SELECT count(*) FROM public.job
          WHERE requested_by = auth.uid() AND status IN ('pending', 'running')
        $$;
        REVOKE ALL ON FUNCTION app_private.is_household_member() FROM PUBLIC;
        REVOKE ALL ON FUNCTION app_private.active_job_count() FROM PUBLIC;
        GRANT USAGE ON SCHEMA app_private TO authenticated;
        GRANT EXECUTE ON FUNCTION app_private.is_household_member() TO authenticated;
        GRANT EXECUTE ON FUNCTION app_private.active_job_count() TO authenticated;
        GRANT USAGE ON SCHEMA public TO authenticated;
        """
    )
    for table in HOUSEHOLD_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"GRANT SELECT ON {table} TO authenticated")
        op.execute(
            f"CREATE POLICY household_read ON {table} FOR SELECT TO authenticated "
            "USING (app_private.is_household_member())"
        )
    op.execute("ALTER TABLE household_member ENABLE ROW LEVEL SECURITY")
    op.execute("GRANT SELECT ON household_member TO authenticated")
    op.execute(
        "CREATE POLICY own_membership ON household_member FOR SELECT TO authenticated "
        "USING (user_id = (SELECT auth.uid()))"
    )
    op.execute("GRANT INSERT (kind, params) ON job TO authenticated")
    op.execute(
        "CREATE POLICY request_meals ON job FOR INSERT TO authenticated "
        "WITH CHECK (kind = 'generate_meals' AND requested_by = (SELECT auth.uid()) "
        "AND app_private.is_household_member() AND app_private.active_job_count() < 3)"
    )
    op.create_table(
        "client_log_batch",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), server_default=sa.text("auth.uid()"), nullable=True),
        sa.Column("device", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("entries", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(entries) = 'array' AND jsonb_array_length(entries) <= 2000",
            name="client_log_batch_entries",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TABLE client_log_batch ENABLE ROW LEVEL SECURITY")
    op.execute("GRANT INSERT (device, entries) ON client_log_batch TO authenticated")
    op.execute(
        "CREATE POLICY send_logs ON client_log_batch FOR INSERT TO authenticated "
        "WITH CHECK (user_id = (SELECT auth.uid()) AND app_private.is_household_member())"
    )
