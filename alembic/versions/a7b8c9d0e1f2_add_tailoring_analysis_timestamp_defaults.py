"""Add server defaults to tailoring analysis timestamps.

Revision ID: a7b8c9d0e1f2
Revises: f4e8d2a6b1c9
"""

from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f4e8d2a6b1c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tailoring_analyses",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        existing_nullable=False,
    )
    op.alter_column(
        "tailoring_analyses",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "tailoring_analyses",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "tailoring_analyses",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=None,
        existing_nullable=False,
    )
