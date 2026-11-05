"""Data access for the CV Tailoring Studio analysis result model."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from backend.models.tailoring_analysis import TailoringAnalysis
from backend.repositories.base import BaseRepository


class TailoringAnalysisRepository(BaseRepository[TailoringAnalysis]):
    """CRUD for :class:`TailoringAnalysis` records."""

    async def get_latest_for_user(
        self,
        user_id: uuid.UUID,
    ) -> TailoringAnalysis | None:
        """Return the most recently created analysis for a user."""
        stmt = (
            self._apply_soft_delete_filter(
                select(TailoringAnalysis).where(
                    TailoringAnalysis.user_id == user_id,
                    TailoringAnalysis.deleted_at.is_(None),
                )
            )
            .order_by(TailoringAnalysis.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> list[TailoringAnalysis]:
        """Return paginated analyses for a user."""
        stmt = (
            self._apply_soft_delete_filter(
                select(TailoringAnalysis).where(
                    TailoringAnalysis.user_id == user_id,
                    TailoringAnalysis.deleted_at.is_(None),
                )
            )
            .order_by(TailoringAnalysis.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_owned_by_id(
        self,
        analysis_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> TailoringAnalysis | None:
        """Return an analysis only if it belongs to the given user."""
        return await self.get_by(id=analysis_id, user_id=user_id)
