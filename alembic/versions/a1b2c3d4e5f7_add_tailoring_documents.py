"""add tailoring documents table for CV Tailoring Studio

Revision ID: a1b2c3d4e5f7
Revises: d7e8f9a0b1c2
Create Date: 2026-10-10

Creates the ``tailoring_documents`` table for storing uploaded CVs and
job descriptions with their extracted text and original raw bytes.

Uses ``batch_alter_table`` for SQLite compatibility.  The ``raw_content``
column uses ``LargeBinary`` (BLOB) which is supported by both SQLite and
PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tailoring_documents",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("raw_content", sa.LargeBinary(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("pasted_text", sa.Text(), nullable=True),
        sa.Column("extraction_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("extraction_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tailoring_documents") as batch_op:
        batch_op.create_index("ix_tailoring_documents_user_id", ["user_id"])


def downgrade() -> None:
    with op.batch_alter_table("tailoring_documents") as batch_op:
        batch_op.drop_index("ix_tailoring_documents_user_id")
    op.drop_table("tailoring_documents")
