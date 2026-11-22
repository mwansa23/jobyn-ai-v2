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
from copy import deepcopy
from typing import Any

from backend.ai.cv_editor import CVEditorError, CVEditorService
from backend.ai.cv_rewriter import CVRewriterError, CVRewriterService
from backend.models.tailored_cv import TailoredCV
from backend.repositories.tailored_cv import TailoredCVRepository
from backend.repositories.tailoring_analysis import TailoringAnalysisRepository
from backend.repositories.tailoring import TailoringDocumentRepository
from backend.services.base import BaseService
from backend.services.cv_validator import CVValidator
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

    async def _resolve_original_cv_text(
        self,
        user_id: uuid.UUID,
        cv_record: TailoredCV | None = None,
    ) -> str:
        """Return the original CV text that *cv_record* was generated against.

        Resolves through ``cv_record.analysis_id → TailoringAnalysis.cv_document_id
        → TailoringDocument``.  This is the document the CV was actually produced
        from, regardless of whether the user has since uploaded a new CV.

        Returns an empty string when the source document cannot be resolved
        (missing analysis_id, deleted analysis, deleted document, or empty
        extracted text).  An empty string causes ``CVValidator.validate()`` to
        return ``skipped=True``, which the export guard blocks.  This is the
        fail-closed behaviour: we never silently substitute an unrelated CV.
        """
        if cv_record is not None and cv_record.analysis_id is not None:
            analysis_repo = self._get_analysis_repo()
            analysis = await analysis_repo.get_by(id=cv_record.analysis_id)
            if analysis is not None:
                doc = await self.document_service.get_document_for_user(
                    analysis.cv_document_id, user_id
                )
                if doc is not None:
                    text = doc.extracted_text or doc.pasted_text or ""
                    if text.strip():
                        return text
        # Source document unavailable — fail closed with empty text.
        return ""

    def _run_validation(
        self,
        content: dict[str, Any],
        original_cv_text: str,
    ) -> None:
        """Run :class:`CVValidator` and embed the result into *content* in-place.

        On success, sets ``content["validation"]`` with ``skipped: False``.
        On any exception, sets ``content["validation"]`` with ``skipped: True``
        so callers can distinguish a technical bypass from a genuine clean pass.
        Never raises.
        """
        try:
            result = CVValidator.validate(
                sections=content.get("sections", []),
                original_cv_text=original_cv_text,
            )
            content["validation"] = result.to_dict()  # carries skipped from result
        except Exception as exc:  # pragma: no cover — validation must not block saves
            logger.warning("CV validation raised an unexpected error: %s", exc)
            content["validation"] = {
                "is_valid": True,
                "skipped": True,
                "unsupported_claims": [],
                "warnings": [],
                "findings": [],
                "validation_summary": "Validation skipped due to an internal error.",
            }

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

        # ── Stale-analysis safeguard ──────────────────────────────────────────
        # Verify that the analysis was run against the *current* CV and JD
        # documents.  If the user uploaded a new CV or JD after the analysis
        # was run, the analysis is stale and must not be silently reused.
        if analysis.cv_document_id != cv_doc.id:
            raise TailoringGenerationError(
                "The saved analysis was run against a different CV. "
                "Please re-run the analysis with the current CV before generating."
            )

        jd_doc = await self.document_service.get_latest_job_description(user_id)
        if jd_doc is None:
            raise TailoringGenerationError(
                "No job description found. Please upload or paste a JD first."
            )
        if analysis.jd_document_id != jd_doc.id:
            raise TailoringGenerationError(
                "The saved analysis was run against a different job description. "
                "Please re-run the analysis with the current JD before generating."
            )

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

        # ── Factual validation (deterministic, no LLM call) ──────────────────
        # cv_text was resolved from cv_doc above and is the correct source
        # document for this generation, so pass it directly (avoids a redundant
        # DB round-trip through _resolve_original_cv_text).
        self._run_validation(rewritten, cv_text)

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
    ) -> TailoredCV | dict[str, Any]:
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

        # Resolve source text from the analysis that produced the *current*
        # latest version — not from whatever CV the user may have uploaded
        # since generation.  This single resolved text is reused for both the
        # LLM editor context (advisory) and the deterministic validator
        # (enforcement), ensuring both operate against the same source CV.
        original_cv_text = await self._resolve_original_cv_text(user_id, latest)

        try:
            edited = await self._cv_editor.apply_edit(
                current_cv=latest.content,
                edit_request=edit_request,
                original_cv_text=original_cv_text,
            )
        except CVEditorError as exc:
            logger.exception("CV edit failed for user %s", user_id)
            raise TailoringGenerationError(f"CV edit failed: {exc}") from exc

        if edited.get("needs_clarification"):
            return edited

        explanation_by_index = {
            entry["section_index"]: entry["explanation"].strip()
            for entry in edited.pop("section_explanations", [])
        }
        edited.pop("needs_clarification", None)
        edited.pop("clarification_question", None)
        edited["comparison"] = self.build_comparison(
            latest.content.get("sections", []),
            edited.get("sections", []),
            explanation_by_index,
            fallback_explanation=(
                f"Change made in response to: {edit_request.strip()}"
            ),
        )

        # Validate the edited content against the same generation-time source
        # CV resolved above — no second lookup needed.
        self._run_validation(edited, original_cv_text)

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

    async def save_visual_edit(
        self,
        user_id: uuid.UUID,
        cv_id: uuid.UUID,
        sections: list[dict[str, Any]],
    ) -> TailoredCV:
        """Save candidate-edited sections as a new version of the latest CV."""
        current = await self.repository.get_owned_by_id(cv_id, user_id)
        if current is None:
            raise TailoringGenerationError("Tailored CV not found.")

        latest = await self.repository.get_latest_for_user(user_id)
        if latest is None or latest.id != current.id:
            raise TailoringGenerationError(
                "This CV version is no longer current. Reload the latest version before editing."
            )

        before_sections = current.content.get("sections", [])
        if before_sections == sections:
            raise TailoringGenerationError("No changes were made to the CV.")

        content = deepcopy(current.content)
        content["sections"] = deepcopy(sections)
        content["comparison"] = self.build_comparison(
            before_sections,
            sections,
            {},
            fallback_explanation="Edited directly by the candidate.",
        )

        # ── F2: Validate the visually edited content against the original ───
        # Resolve source document from the analysis that produced this CV
        # version, not from whatever CV has been uploaded most recently.
        original_cv_text = await self._resolve_original_cv_text(user_id, current)
        self._run_validation(content, original_cv_text)

        version_number = await self.repository.get_max_version_number(user_id) + 1
        cv_record = await self.repository.create(
            user_id=user_id,
            analysis_id=current.analysis_id,
            version_number=version_number,
            status="pending",
            content=content,
            edit_type="visual_edit",
            edit_request="Edited in the visual CV editor.",
            change_summary="Updated directly in the visual editor.",
        )
        await self.commit()
        return await self.repository.get_by(id=cv_record.id) or cv_record

    @staticmethod
    def build_comparison(
        before_sections: list[Any],
        after_sections: list[Any],
        explanation_by_index: dict[int, str],
        *,
        fallback_explanation: str,
    ) -> list[dict[str, Any]]:
        """Describe changes by matching sections, not by their current position."""
        def identity(section: Any) -> tuple[str, str]:
            if not isinstance(section, dict):
                return ("", "")
            return (
                str(section.get("heading") or "").strip().casefold(),
                str(section.get("type") or "").strip().casefold(),
            )

        before_by_identity: dict[tuple[str, str], list[int]] = {}
        for index, section in enumerate(before_sections):
            before_by_identity.setdefault(identity(section), []).append(index)

        after_to_before: dict[int, int] = {}
        unmatched_after = []
        matched_before: set[int] = set()
        for after_index, section in enumerate(after_sections):
            candidates = before_by_identity.get(identity(section), [])
            before_index = next(
                (candidate for candidate in candidates if candidate not in matched_before),
                None,
            )
            if before_index is None:
                unmatched_after.append(after_index)
            else:
                after_to_before[after_index] = before_index
                matched_before.add(before_index)

        # A renamed heading can still be matched when its section type is unique.
        for after_index in unmatched_after[:]:
            after_section = after_sections[after_index]
            after_type = (
                str(after_section.get("type") or "").strip().casefold()
                if isinstance(after_section, dict)
                else ""
            )
            candidates = [
                index
                for index, section in enumerate(before_sections)
                if index not in matched_before
                and isinstance(section, dict)
                and str(section.get("type") or "").strip().casefold() == after_type
            ]
            remaining_after = [
                index
                for index in unmatched_after
                if isinstance(after_sections[index], dict)
                and str(after_sections[index].get("type") or "").strip().casefold()
                == after_type
            ]
            if after_type and len(candidates) == 1 and len(remaining_after) == 1:
                before_index = candidates[0]
                after_to_before[after_index] = before_index
                matched_before.add(before_index)
                unmatched_after.remove(after_index)

        comparison = []
        for after_index, after in enumerate(after_sections):
            before_index = after_to_before.get(after_index)
            before = before_sections[before_index] if before_index is not None else None
            moved = before_index is not None and before_index != after_index
            status = (
                "added" if before is None else
                "changed" if before != after else
                "unchanged"
            )
            section = after if isinstance(after, dict) else {}
            entry = {
                "section_index": after_index,
                "heading": section.get("heading") or section.get("type") or "Section",
                "status": status,
                "before": deepcopy(before),
                "after": deepcopy(after),
            }
            if before_index is not None:
                entry["before_index"] = before_index
            if moved:
                entry["position_changed"] = True
            if status != "unchanged":
                entry["explanation"] = (
                    explanation_by_index.get(after_index)
                    or (
                        "Section position changed as part of this edit."
                        if moved and before == after
                        else fallback_explanation
                    )
                    or "Changed in response to the requested CV edit."
                )
            comparison.append(entry)

        for before_index, before in enumerate(before_sections):
            if before_index in matched_before:
                continue
            section = before if isinstance(before, dict) else {}
            comparison.append(
                {
                    "section_index": before_index,
                    "heading": section.get("heading") or section.get("type") or "Section",
                    "status": "removed",
                    "before": deepcopy(before),
                    "after": None,
                    "before_index": before_index,
                    "explanation": fallback_explanation
                    or "Section removed in response to the requested CV edit.",
                }
            )

        comparison.sort(
            key=lambda entry: (
                entry.get("before_index", entry["section_index"]),
                entry["section_index"],
            )
        )
        return comparison

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
        reverted_content = deepcopy(target.content)

        # ── F6: Revalidate against the original source CV ───────────────────
        # The old validation result embedded in target.content may be stale —
        # the user may have uploaded a new CV since target was created.
        # Resolve source text from the analysis linked to *target*, not from
        # get_latest_cv(), to compare against the document that produced it.
        original_cv_text = await self._resolve_original_cv_text(user_id, target)
        self._run_validation(reverted_content, original_cv_text)

        cv_record = await self.repository.create(
            user_id=user_id,
            analysis_id=target.analysis_id,
            version_number=new_version,
            status="pending",
            content=reverted_content,
            edit_type="revert",
            edit_request=f"Revert to version {version_number}",
            change_summary=f"Reverted to version {version_number}.",
        )
        await self.commit()

        return await self.repository.get_by(id=cv_record.id) or cv_record
