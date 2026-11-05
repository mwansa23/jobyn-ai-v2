"""CV Tailoring Studio document upload, extraction, and management service.

Reuses the existing :mod:`backend.services.resume_parser` for text extraction
from PDF and DOCX files.  Stores original file bytes so documents can be
re-processed without re-upload.
"""

from __future__ import annotations

import logging
import uuid

from backend.models.enums import DocumentType, ExtractionStatus, SourceType
from backend.models.tailoring import TailoringDocument
from backend.repositories.tailoring import TailoringDocumentRepository
from backend.services.base import BaseService
from backend.services.resume_parser import (
    EmptyDocumentError,
    ResumeParserError,
    UnsupportedFileTypeError,
    extract_text,
    validate_upload,
)

logger = logging.getLogger(__name__)


class TailoringUploadError(Exception):
    """Raised when a tailoring document upload or extraction fails."""


class TailoringService(BaseService[TailoringDocumentRepository]):
    """Manages CV and job-description uploads, extraction, and retrieval."""

    def __init__(self, repository: TailoringDocumentRepository) -> None:
        super().__init__(repository)
        self.repository = repository

    async def upload_document(
        self,
        user_id: uuid.UUID,
        document_type: DocumentType,
        filename: str,
        content_type: str,
        file_bytes: bytes,
    ) -> TailoringDocument:
        """Validate, extract text from, and persist an uploaded document.

        Args:
            user_id: The authenticated user's UUID.
            document_type: ``CV`` or ``JOB_DESCRIPTION``.
            filename: Original filename as reported by the client.
            content_type: MIME type of the uploaded file.
            file_bytes: Raw file content.

        Returns:
            The persisted :class:`TailoringDocument` record.

        Raises:
            TailoringUploadError: If validation or extraction fails.
        """
        size_bytes = len(file_bytes)

        # Step 1 — validate before touching the database.
        try:
            validate_upload(content_type, size_bytes)
        except ResumeParserError as exc:
            raise TailoringUploadError(str(exc)) from exc

        # Step 2 — create the record with PROCESSING status.
        doc = await self.repository.create(
            user_id=user_id,
            document_type=document_type,
            source_type=SourceType.MANUAL,
            original_filename=filename,
            content_type=content_type,
            file_size_bytes=size_bytes,
            raw_content=file_bytes,
            extraction_status=ExtractionStatus.PROCESSING,
        )
        await self.commit()

        # Step 3 — extract text.
        try:
            text = extract_text(file_bytes, content_type)
            await self.repository.update(
                doc,
                extracted_text=text,
                extraction_status=ExtractionStatus.SUCCEEDED,
                extraction_error=None,
            )
            await self.commit()
        except EmptyDocumentError as exc:
            # The file is valid but yielded no text — likely a scanned PDF.
            await self.repository.update(
                doc,
                extraction_status=ExtractionStatus.SCANNED_PDF,
                extraction_error=str(exc),
            )
            await self.commit()
            raise TailoringUploadError(
                "The document appears to be a scanned image. "
                "Text extraction is not available for scanned PDFs yet. "
                "Please paste the text manually instead."
            ) from exc
        except ResumeParserError as exc:
            await self.repository.update(
                doc,
                extraction_status=ExtractionStatus.FAILED,
                extraction_error=str(exc),
            )
            await self.commit()
            raise TailoringUploadError(str(exc)) from exc

        return doc

    async def paste_job_description(
        self,
        user_id: uuid.UUID,
        text: str,
    ) -> TailoringDocument:
        """Create a job-description document from pasted text.

        Args:
            user_id: The authenticated user's UUID.
            text: The pasted job-description text.

        Returns:
            The persisted :class:`TailoringDocument` record.

        Raises:
            TailoringUploadError: If the text is empty.
        """
        stripped = text.strip()
        if not stripped:
            raise TailoringUploadError("Job description text cannot be empty.")
        if len(stripped) > 50000:
            raise TailoringUploadError(
                "Job description text is too long. Maximum 50,000 characters."
            )

        doc = await self.repository.create(
            user_id=user_id,
            document_type=DocumentType.JOB_DESCRIPTION,
            source_type=SourceType.MANUAL,
            pasted_text=stripped,
            extracted_text=stripped,
            extraction_status=ExtractionStatus.SUCCEEDED,
        )
        await self.commit()
        return doc

    async def get_latest_cv(self, user_id: uuid.UUID) -> TailoringDocument | None:
        """Return the user's most recent CV document, or None."""
        return await self.repository.get_latest_for_user(user_id, DocumentType.CV)

    async def get_latest_job_description(self, user_id: uuid.UUID) -> TailoringDocument | None:
        """Return the user's most recent job-description document, or None."""
        return await self.repository.get_latest_for_user(user_id, DocumentType.JOB_DESCRIPTION)

    async def list_documents(
        self,
        user_id: uuid.UUID,
        *,
        document_type: DocumentType | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[TailoringDocument]:
        """Return paginated documents for a user."""
        return await self.repository.list_for_user(
            user_id, document_type=document_type, offset=offset, limit=limit,
        )

    async def get_document_for_user(
        self,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> TailoringDocument | None:
        """Return a document only if it belongs to the user (ownership check)."""
        return await self.repository.get_owned_by_id(document_id, user_id)

    async def delete_document(self, document_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Soft-delete a document owned by the user. Returns True if deleted."""
        doc = await self.repository.get_owned_by_id(document_id, user_id)
        if doc is None:
            return False
        await self.repository.delete(doc)
        await self.commit()
        return True


__all__ = ["TailoringService", "TailoringUploadError"]
