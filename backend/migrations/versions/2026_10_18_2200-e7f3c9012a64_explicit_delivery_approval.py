"""Explicit alternative delivery approval and purchase guard.

Revision ID: e7f3c9012a64
Revises: d9a71b0e962f
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7f3c9012a64"
down_revision: str | None = "d9a71b0e962f"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    kind = sa.Enum(
        "selected",
        "approved",
        "proposed",
        "reapproved",
        "deferred",
        "authorized",
        "booked",
        "failed",
        "unconfirmed",
        "confirmed_present",
        "confirmed_absent",
        name="delivery_decision_kind",
    )
    op.create_table(
        "delivery_decision",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("grocery_order_id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("kind", kind, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("job_id", sa.Uuid()),
        sa.Column("proposal_id", sa.Uuid()),
        sa.Column("delivery_date", sa.Date()),
        sa.Column("delivery_by", sa.Time()),
        sa.Column("next_delivery_date", sa.Date()),
        sa.Column("window", postgresql.JSONB()),
        sa.Column("issue", postgresql.JSONB()),
        sa.Column("booking", postgresql.JSONB()),
        sa.Column("authorized_total_cents", sa.Integer()),
        sa.Column("worker_stop", postgresql.JSONB()),
    )
    op.create_index(
        "ix_delivery_decision_grocery_order_id", "delivery_decision", ["grocery_order_id"]
    )
    op.create_index("ix_delivery_decision_household_id", "delivery_decision", ["household_id"])
    op.add_column(
        "grocery_order", sa.Column("accepted_delivery_proposal_id", sa.Uuid(), nullable=True)
    )
    op.add_column("job", sa.Column("worker_task_arn", sa.String(), nullable=True))
    op.add_column("job", sa.Column("worker_stop", postgresql.JSONB(), nullable=True))
    op.add_column("grocery_order", sa.Column("click_job_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "grocery_order_click_job_id_fkey",
        "grocery_order",
        "job",
        ["click_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "grocery_order_proposal_before_click",
        "grocery_order",
        "clicked_at IS NULL OR coalesce(book_issue->'proposal', 'null'::jsonb) = 'null'::jsonb",
    )


def downgrade() -> None:
    op.drop_constraint("grocery_order_click_job_id_fkey", "grocery_order", type_="foreignkey")
    op.drop_column("grocery_order", "click_job_id")
    op.drop_column("job", "worker_stop")
    op.drop_column("job", "worker_task_arn")
    op.drop_table("delivery_decision")
    sa.Enum(name="delivery_decision_kind").drop(op.get_bind())
    op.drop_constraint("grocery_order_proposal_before_click", "grocery_order", type_="check")
    op.drop_column("grocery_order", "accepted_delivery_proposal_id")
