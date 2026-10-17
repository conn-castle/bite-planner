"""Order-owned recipe edit proposals and recipe revisions.

Revision ID: c6e4a291b502
Revises: a4f71ce308bd
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6e4a291b502"
down_revision: str | Sequence[str] | None = "a4f71ce308bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKS: dict[str, str] = {
    "job_edit_meal": "(kind::text = 'edit_meal') = (meal_id IS NOT NULL)",
    "job_edit_request": "(kind::text = 'edit_meal') = (meal_edit IS NOT NULL)",
    "job_edit_proposal": "kind::text = 'edit_meal' OR proposal IS NULL",
    "job_edit_success": (
        "kind::text <> 'edit_meal' OR (status::text = 'succeeded') = (proposal IS NOT NULL)"
    ),
    "job_edit_decision": "decision IS NULL OR kind::text = 'edit_meal'",
    "job_edit_accept": "decision::text <> 'accepted' OR status::text = 'succeeded'",
    "job_edit_reject": "decision::text <> 'rejected' OR status::text IN ('succeeded', 'failed')",
    "job_edit_decided": (
        "(decision IS NOT NULL) = (decided_by IS NOT NULL) AND "
        "(decision IS NOT NULL) = (decided_at IS NOT NULL)"
    ),
}


def upgrade() -> None:
    op.execute("ALTER TYPE job_kind ADD VALUE 'edit_meal'")
    op.add_column("meal", sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
    decision = postgresql.ENUM("accepted", "rejected", name="edit_decision")
    decision.create(op.get_bind())
    op.add_column("job", sa.Column("meal_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "job_meal_id_fkey", "job", "meal", ["meal_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_job_meal_id", "job", ["meal_id"])
    for name in ("meal_edit", "proposal"):
        op.add_column("job", sa.Column(name, postgresql.JSONB(), nullable=True))
    op.add_column("job", sa.Column("decision", decision, nullable=True))
    op.add_column("job", sa.Column("decided_by", sa.Uuid(), nullable=True))
    op.add_column("job", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    for name, expression in CHECKS.items():
        op.create_check_constraint(name, "job", expression)
    op.create_index(
        "job_one_undecided_edit",
        "job",
        ["meal_id"],
        unique=True,
        postgresql_where=sa.text("meal_id IS NOT NULL AND decision IS NULL"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM job WHERE kind::text = 'edit_meal'")
    op.drop_index("job_one_undecided_edit", table_name="job")
    for name in CHECKS:
        op.drop_constraint(name, "job", type_="check")
    op.drop_index("ix_job_meal_id", table_name="job")
    op.drop_constraint("job_meal_id_fkey", "job", type_="foreignkey")
    for name in ("meal_id", "meal_edit", "proposal", "decision", "decided_by", "decided_at"):
        op.drop_column("job", name)
    op.execute("DROP TYPE edit_decision")
    op.drop_column("meal", "revision")
    op.execute("ALTER TYPE job_kind RENAME TO job_kind_old")
    sa.Enum("generate_meals", "build_list", "shop", "quote", "book", name="job_kind").create(
        op.get_bind()
    )
    op.execute("ALTER TABLE job ALTER COLUMN kind TYPE job_kind USING kind::text::job_kind")
    op.execute("DROP TYPE job_kind_old")
