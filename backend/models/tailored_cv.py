"""Tailored CV version model.

Stores each version of a candidate's tailored CV as a JSON blob, linked to
the analysis that produced it. Versions are immutable once created — each
edit creates a new version record. This preserves the full revision history
so the candidate can undo or recover earlier versions.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship

from backend.database.base_class import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.models.tailoring_analysis import TailoringAnalysis
    from backend.models.user import User


class TailoredCV(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A single version of a candidate's tailored CV.

    Each record is immutable. When the candidate requests an edit, a new
    version is created with the updated content. The ``version_number``
    increments sequentially per user. ``status`` tracks whether the version
    was accepted, rejected, or is pending review.
    """

    __tablename__ = "tailored_cvs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tailoring_analyses.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending",
        # pending | accepted | rejected
    )

    # The complete tailored CV as structured JSON (sections list + metadata).
    content: Mapped[dict] = mapped_column(JSON, nullable=False)

    # How this version was created: "initial" (from rewrite), "edit" (from conversation).
    edit_type: Mapped[str] = mapped_column(String(20), nullable=False, default="initial")

    # For "edit" versions: the candidate's natural-language request.
    edit_request: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Brief summary of what changed in this version.
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = orm_relationship()
    analysis: Mapped[TailoringAnalysis | None] = orm_relationship()
