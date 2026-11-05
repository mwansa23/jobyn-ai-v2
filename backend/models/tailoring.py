"""CV Tailoring Studio document models.

Stores uploaded CVs and job descriptions with their extracted text and
original raw bytes, associated with the authenticated user who uploaded them.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship

from backend.database.base_class import Base
from backend.models.enums import (
    DocumentType,
    ExtractionStatus,
    SourceType,
    enum_column,
)
from backend.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.models.user import User


class TailoringDocument(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """An uploaded CV or job-description document for the Tailoring Studio.

    Raw file bytes are stored in ``raw_content`` so that originals are preserved
    and can be re-processed without requiring the user to re-upload.  Pasted
    text (no file) stores the text directly in ``pasted_text``.
    """

    __tablename__ = "tailoring_documents"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    document_type: Mapped[DocumentType] = mapped_column(
        enum_column(DocumentType),
        nullable=False,
    )
    source_type: Mapped[SourceType] = mapped_column(
        enum_column(SourceType),
        nullable=False,
        default=SourceType.MANUAL,
        server_default=SourceType.MANUAL.value,
    )

    # File metadata (nullable when text is pasted directly)
    original_filename: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    content_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    file_size_bytes: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    # Original file bytes (preserved for re-processing)
    raw_content: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True,
    )

    # Extracted or pasted text
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pasted_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Extraction lifecycle
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        enum_column(ExtractionStatus),
        nullable=False,
        default=ExtractionStatus.PENDING,
        server_default=ExtractionStatus.PENDING.value,
    )
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Soft-deletion is handled by SoftDeleteMixin.deleted_at.

    user: Mapped[User] = orm_relationship(back_populates="tailoring_documents")
