"""Data access for the CV Tailoring Studio document model."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from backend.models.enums import DocumentType
from backend.models.tailoring import TailoringDocument
from backend.repositories.base import BaseRepository


class TailoringDocumentRepository(BaseRepository[TailoringDocument]):
    """CRUD for :class:`TailoringDocument` records."""

    async def get_latest_for_user(
        self,
        user_id: uuid.UUID,
        document_type: DocumentType,
    ) -> TailoringDocument | None:
        """Return the most recently created document of the given type for a user."""
        stmt = (
            self._apply_soft_delete_filter(
                select(TailoringDocument).where(
                    TailoringDocument.user_id == user_id,
                    TailoringDocument.document_type == document_type,
                    TailoringDocument.deleted_at.is_(None),
                )
            )
            .order_by(TailoringDocument.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        document_type: DocumentType | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[TailoringDocument]:
        """Return paginated documents for a user, optionally filtered by type."""
        stmt = self._apply_soft_delete_filter(
            select(TailoringDocument).where(
                TailoringDocument.user_id == user_id,
                TailoringDocument.deleted_at.is_(None),
            )
        )
        if document_type is not None:
            stmt = stmt.where(TailoringDocument.document_type == document_type)
        stmt = stmt.order_by(TailoringDocument.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_owned_by_id(
        self,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> TailoringDocument | None:
        """Return a document only if it belongs to the given user (ownership check)."""
        return await self.get_by(id=document_id, user_id=user_id)
