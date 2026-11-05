"""CV tailoring generation and conversational editing service.

Orchestrates the full Phase 3 pipeline:
1. Load the user's latest analysis (from Phase 2).
2. Call the CV rewriter to produce the initial tailored CV.
3. Persist as version 1.
4. For conversational edits: load the latest version, call the CV editor,
   persist as a new version.
5. Support accept/reject of versions and version recovery.

This service coordinates the AI layer (``cv_rewriter``, ``cv_editor``) with
the data layer (``TailoredCVRepository``, ``TailoringAnalysisRepository``,
``TailoringService``).
"""

from __future__ import annotations

import logging
import uuid

from backend.ai.cv_editor import CVEditorError, CVEditorService
from backend.ai.cv_rewriter import CVRewriterError, CVRewriterService
from backend.models.tailored_cv import TailoredCV
from backend.repositories.tailored_cv import TailoredCVRepository
from backend.repositories.tailoring_analysis import TailoringAnalysisRepository
from backend.repositories.tailoring import TailoringDocumentRepository
from backend.services.base import BaseService
from backend.services.tailoring_service import TailoringService

logger = logging.getLogger(__name__)


class TailoringGenerationError(Exception):
    """Raised when CV generation or editing cannot complete."""


class TailoringGenerationService(BaseService[TailoredCVRepository]):
    """Orchestrates tailored CV generation and conversational editing."""

    def __init__(
        self,
        repository: TailoredCVRepository,
        document_service: TailoringService,
        analysis_repository: TailoringAnalysisRepository | None = None,
        cv_rewriter: CVRewriterService | None = None,
        cv_editor: CVEditorService | None = None,
    ) -> None:
        super().__init__(repository)
        self.repository = repository
        self.document_service = document_service
        self._analysis_repository = analysis_repository
        self._cv_rewriter = cv_rewriter or CVRewriterService()
        self._cv_editor = cv_editor or CVEditorService()

    def _get_analysis_repo(self) -> TailoringAnalysisRepository:
        if self._analysis_repository is not None:
            return self._analysis_repository
        return TailoringAnalysisRepository(
            session=self.repository.session,
            model=None,  # type: ignore[arg-type]
        )

    async def generate_tailored_cv(self, user_id: uuid.UUID) -> TailoredCV:
        """Generate the initial tailored CV from the user's latest analysis.

        Loads the user's latest Phase 2 analysis, calls the CV rewriter, and
        persists the result as version 1 (or the next version number if previous
        versions exist).

        Args:
            user_id: The authenticated user's UUID.

        Returns:
            The persisted :class:`TailoredCV` record.

        Raises:
            TailoringGenerationError: If no analysis exists or generation fails.
        """
        # Load the latest analysis
        analysis_repo = self._get_analysis_repo()
        analysis = await analysis_repo.get_latest_for_user(user_id)
        if analysis is None:
            raise TailoringGenerationError(
                "No analysis found. Run analysis (Phase 2) before generating a tailored CV."
            )
        if analysis.error:
            raise TailoringGenerationError(
                "The latest analysis has errors. Please re-run the analysis first."
            )
        if not analysis.recommendations:
            raise TailoringGenerationError(
                "The latest analysis is incomplete (no recommendations). "
                "Please re-run the analysis."
            )

        # Load original CV text
        cv_doc = await self.document_service.get_latest_cv(user_id)
        if cv_doc is None:
            raise TailoringGenerationError("No CV found. Please upload a CV first.")

        cv_text = cv_doc.extracted_text or cv_doc.pasted_text or ""
        if not cv_text.strip():
            raise TailoringGenerationError("CV document has no extracted text.")

        # Call the CV rewriter
        try:
            rewritten = await self._cv_rewriter.rewrite(
                cv_text=cv_text,
                jd_analysis=analysis.jd_analysis or {},
                match_result=analysis.match_result or {},
                recommendations=analysis.recommendations or {},
            )
        except CVRewriterError as exc:
            logger.exception("CV rewrite failed for user %s", user_id)
            raise TailoringGenerationError(f"CV rewriting failed: {exc}") from exc

        # Determine version number
        max_version = await self.repository.get_max_version_number(user_id)
        version_number = max_version + 1

        # Persist
        cv_record = await self.repository.create(
            user_id=user_id,
            analysis_id=analysis.id,
            version_number=version_number,
            status="pending",
            content=rewritten,
            edit_type="initial",
            change_summary=rewritten.get("generation_notes", "Initial tailored CV generated."),
        )
        await self.commit()

        return await self.repository.get_by(id=cv_record.id) or cv_record

    async def apply_edit(
        self,
        user_id: uuid.UUID,
        edit_request: str,
    ) -> TailoredCV:
        """Apply a conversational edit to the latest tailored CV.

        Args:
            user_id: The authenticated user's UUID.
            edit_request: The candidate's natural-language edit request.

        Returns:
            A new :class:`TailoredCV` record with the updated content.

        Raises:
            TailoringGenerationError: If no CV exists or the edit fails.
        """
        latest = await self.repository.get_latest_for_user(user_id)
        if latest is None:
            raise TailoringGenerationError(
                "No tailored CV found. Generate one first."
            )

        # Load original CV text for integrity reference
        cv_doc = await self.document_service.get_latest_cv(user_id)
        original_cv_text = ""
        if cv_doc:
            original_cv_text = cv_doc.extracted_text or cv_doc.pasted_text or ""

        try:
            edited = await self._cv_editor.apply_edit(
                current_cv=latest.content,
                edit_request=edit_request,
                original_cv_text=original_cv_text,
            )
        except CVEditorError as exc:
            logger.exception("CV edit failed for user %s", user_id)
            raise TailoringGenerationError(f"CV edit failed: {exc}") from exc

        max_version = await self.repository.get_max_version_number(user_id)
        version_number = max_version + 1

        cv_record = await self.repository.create(
            user_id=user_id,
            analysis_id=latest.analysis_id,
            version_number=version_number,
            status="pending",
            content=edited,
            edit_type="edit",
            edit_request=edit_request,
            change_summary=edited.get("edit_summary", "CV edited per candidate request."),
        )
        await self.commit()

        return await self.repository.get_by(id=cv_record.id) or cv_record

    async def get_latest_for_user(self, user_id: uuid.UUID) -> TailoredCV | None:
        """Return the user's most recent tailored CV version."""
        return await self.repository.get_latest_for_user(user_id)

    async def get_by_version(
        self,
        user_id: uuid.UUID,
        version_number: int,
    ) -> TailoredCV | None:
        """Return a specific version for the user."""
        return await self.repository.get_by_version(user_id, version_number)

    async def list_versions(
        self,
        user_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> list[TailoredCV]:
        """Return paginated CV versions for a user, newest first."""
        return await self.repository.list_for_user(user_id, offset=offset, limit=limit)

    async def get_owned_by_id(
        self,
        cv_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> TailoredCV | None:
        """Return a CV only if it belongs to the given user."""
        return await self.repository.get_owned_by_id(cv_id, user_id)

    async def set_status(
        self,
        cv_id: uuid.UUID,
        user_id: uuid.UUID,
        status: str,
    ) -> TailoredCV | None:
        """Accept or reject a CV version.

        Args:
            cv_id: The CV version ID.
            user_id: The authenticated user's UUID (ownership check).
            status: 'accepted' or 'rejected'.

        Returns:
            The updated CV record, or None if not found/not owned.
        """
        if status not in ("accepted", "rejected"):
            raise TailoringGenerationError(
                f"Invalid status '{status}'. Use 'accepted' or 'rejected'."
            )

        cv = await self.repository.get_owned_by_id(cv_id, user_id)
        if cv is None:
            return None

        await self.repository.update(cv, status=status)
        await self.commit()
        return await self.repository.get_by(id=cv_id) or cv

    async def revert_to_version(
        self,
        user_id: uuid.UUID,
        version_number: int,
    ) -> TailoredCV:
        """Create a new version that is a copy of an earlier version.

        This effectively "undoes" later changes by creating a new version
        with the content of the specified earlier version.

        Args:
            user_id: The authenticated user's UUID.
            version_number: The version to revert to.

        Returns:
            A new :class:`TailoredCV` record with the reverted content.

        Raises:
            TailoringGenerationError: If the version doesn't exist.
        """
        target = await self.repository.get_by_version(user_id, version_number)
        if target is None:
            raise TailoringGenerationError(
                f"Version {version_number} not found."
            )

        max_version = await self.repository.get_max_version_number(user_id)
        new_version = max_version + 1

        cv_record = await self.repository.create(
            user_id=user_id,
            analysis_id=target.analysis_id,
            version_number=new_version,
            status="pending",
            content=target.content,
            edit_type="revert",
            edit_request=f"Revert to version {version_number}",
            change_summary=f"Reverted to version {version_number}.",
        )
        await self.commit()

        return await self.repository.get_by(id=cv_record.id) or cv_record
