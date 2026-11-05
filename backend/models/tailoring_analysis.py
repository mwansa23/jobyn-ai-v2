"""CV Tailoring Studio analysis result model.

Stores the output of JD analysis, CV-to-JD matching, and tailoring
recommendations as JSON columns, linked to the CV and JD documents that
produced them. This avoids a database migration for schema changes within
the JSON — the analysis structure can evolve without altering the table.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship

from backend.database.base_class import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.models.tailoring import TailoringDocument
    from backend.models.user import User


class TailoringAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """The result of analysing a CV against a job description.

    Each record links a CV document and a JD document for a user and stores
    three JSON blobs produced by the AI pipeline:

    - ``jd_analysis``: structured breakdown of the job description.
    - ``match_result``: evidence-based CV-to-JD comparison.
    - ``recommendations``: structured tailoring recommendations.
    """

    __tablename__ = "tailoring_analyses"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    cv_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tailoring_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    jd_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tailoring_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # The three pipeline stages stored as JSON (works on both SQLite and PostgreSQL).
    jd_analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    match_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    recommendations: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Human-readable error if any stage failed.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = orm_relationship()
    cv_document: Mapped[TailoringDocument] = orm_relationship(
        foreign_keys=[cv_document_id],
    )
    jd_document: Mapped[TailoringDocument] = orm_relationship(
        foreign_keys=[jd_document_id],
    )
