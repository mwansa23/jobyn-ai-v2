"""Add tailoring_analyses table.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f7
Create Date: 2026-10-02 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tailoring_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("cv_document_id", sa.Uuid(), nullable=False),
        sa.Column("jd_document_id", sa.Uuid(), nullable=False),
        sa.Column("jd_analysis", sa.JSON(), nullable=True),
        sa.Column("match_result", sa.JSON(), nullable=True),
        sa.Column("recommendations", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cv_document_id"], ["tailoring_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["jd_document_id"], ["tailoring_documents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_tailoring_analyses_user_id", "tailoring_analyses", ["user_id"])
    op.create_index("ix_tailoring_analyses_cv_document_id", "tailoring_analyses", ["cv_document_id"])
    op.create_index("ix_tailoring_analyses_jd_document_id", "tailoring_analyses", ["jd_document_id"])


def downgrade() -> None:
    op.drop_index("ix_tailoring_analyses_jd_document_id", table_name="tailoring_analyses")
    op.drop_index("ix_tailoring_analyses_cv_document_id", table_name="tailoring_analyses")
    op.drop_index("ix_tailoring_analyses_user_id", table_name="tailoring_analyses")
    op.drop_table("tailoring_analyses")
