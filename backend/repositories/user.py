"""Data access for the :class:`User` model."""

from uuid import UUID

from sqlalchemy import func, select, update

from backend.models.user import User
from backend.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """User-specific queries layered on the generic async repository."""

    async def get_by_email(self, email: str) -> User | None:
        """Fetch an active (non-soft-deleted) user by normalized email."""
        return await self.get_by(email=email)

    async def get_by_supabase_user_id(self, supabase_user_id: UUID) -> User | None:
        """Fetch an active account mapped to a Supabase identity."""
        return await self.get_by(supabase_user_id=supabase_user_id)

    async def get_email_link_candidates(self, email: str) -> list[User]:
        """Find all accounts matching a normalized email, including deleted rows."""
        result = await self.session.execute(
            select(User).where(func.lower(func.trim(User.email)) == email)
        )
        return list(result.scalars().all())

    async def link_supabase_identity(self, user_id: UUID, supabase_user_id: UUID) -> bool:
        """Atomically set a mapping only while the Jobyn account remains unmapped."""
        result = await self.session.execute(
            update(User)
            .where(
                User.id == user_id,
                User.supabase_user_id.is_(None),
                User.deleted_at.is_(None),
            )
            .values(supabase_user_id=supabase_user_id)
            .returning(User.id)
        )
        return result.scalar_one_or_none() is not None
