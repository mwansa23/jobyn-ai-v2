"""Data access for the tailored CV version model."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from backend.models.tailored_cv import TailoredCV
from backend.repositories.base import BaseRepository


class TailoredCVRepository(BaseRepository[TailoredCV]):
    """CRUD for :class:`TailoredCV` records."""

    async def get_latest_for_user(
        self,
        user_id: uuid.UUID,
    ) -> TailoredCV | None:
        """Return the user's most recent tailored CV version (by version_number)."""
        stmt = (
            self._apply_soft_delete_filter(
                select(TailoredCV).where(
                    TailoredCV.user_id == user_id,
                    TailoredCV.deleted_at.is_(None),
                )
            )
            .order_by(TailoredCV.version_number.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_version(
        self,
        user_id: uuid.UUID,
        version_number: int,
    ) -> TailoredCV | None:
        """Return a specific version for the user."""
        stmt = (
            self._apply_soft_delete_filter(
                select(TailoredCV).where(
                    TailoredCV.user_id == user_id,
                    TailoredCV.version_number == version_number,
                    TailoredCV.deleted_at.is_(None),
                )
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> list[TailoredCV]:
        """Return paginated CV versions for a user, newest first."""
        stmt = (
            self._apply_soft_delete_filter(
                select(TailoredCV).where(
                    TailoredCV.user_id == user_id,
                    TailoredCV.deleted_at.is_(None),
                )
            )
            .order_by(TailoredCV.version_number.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_max_version_number(self, user_id: uuid.UUID) -> int:
        """Return the highest version_number for the user, or 0 if none."""
        stmt = (
            select(TailoredCV.version_number)
            .where(
                TailoredCV.user_id == user_id,
                TailoredCV.deleted_at.is_(None),
            )
            .order_by(TailoredCV.version_number.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return row if row is not None else 0

    async def get_owned_by_id(
        self,
        cv_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> TailoredCV | None:
        """Return a CV only if it belongs to the given user."""
        return await self.get_by(id=cv_id, user_id=user_id)
