"""Add tailored_cvs table.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-02 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tailored_cvs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("edit_type", sa.String(length=20), nullable=False, server_default="initial"),
        sa.Column("edit_request", sa.Text(), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["analysis_id"], ["tailoring_analyses.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_tailored_cvs_user_id", "tailored_cvs", ["user_id"])
    op.create_index("ix_tailored_cvs_analysis_id", "tailored_cvs", ["analysis_id"])


def downgrade() -> None:
    op.drop_index("ix_tailored_cvs_analysis_id", table_name="tailored_cvs")
    op.drop_index("ix_tailored_cvs_user_id", table_name="tailored_cvs")
    op.drop_table("tailored_cvs")
