"""Add nullable Supabase identity mapping to users.

Revision ID: f4e8d2a6b1c9
Revises: c3d4e5f6a7b8
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4e8d2a6b1c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("supabase_user_id", sa.Uuid(), nullable=True))
        batch_op.alter_column(
            "hashed_password",
            existing_type=sa.String(length=255),
            nullable=True,
        )
        batch_op.create_index(
            "ix_users_supabase_user_id",
            ["supabase_user_id"],
            unique=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    mapped_users = connection.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE supabase_user_id IS NOT NULL")
    ).scalar_one()
    if mapped_users:
        raise RuntimeError("Cannot remove Supabase identity mappings while mapped users exist.")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_supabase_user_id")
        batch_op.drop_column("supabase_user_id")
