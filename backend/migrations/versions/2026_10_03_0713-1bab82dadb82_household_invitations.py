"""Household invitations

Revision ID: 1bab82dadb82
Revises: 793970a42cfb
Create Date: 2026-10-03 07:13:15.492114

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1bab82dadb82"
down_revision: str | Sequence[str] | None = "793970a42cfb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "household_invite",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["household_id"], ["household.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "household_invite_email",
        "household_invite",
        [sa.literal_column("lower(email)")],
        unique=True,
    )
    op.create_index(
        op.f("ix_household_invite_household_id"), "household_invite", ["household_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_household_invite_household_id"), table_name="household_invite")
    op.drop_index("household_invite_email", table_name="household_invite")
    op.drop_table("household_invite")
