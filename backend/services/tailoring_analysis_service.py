"""CV Tailoring Studio analysis orchestration service.

Loads the user's latest CV and JD documents from the database, runs the
three-stage AI pipeline (JD analysis → CV-to-JD matching → tailoring
recommendations), and persists the results.

This service coordinates the AI layer (:mod:`backend.ai.jd_analyzer`,
:mod:`backend.ai.cv_jd_matcher`, :mod:`backend.ai.tailoring_advisor`) with
the data layer (:class:`TailoringDocumentRepository`,
:class:`TailoringAnalysisRepository`).
"""

from __future__ import annotations

import logging
import uuid

from backend.ai.cv_jd_matcher import CVJDMatcherError, CVJDMatcherService
from backend.ai.jd_analyzer import JDAnalysisError, JDAnalyzerService
from backend.ai.tailoring_advisor import TailoringAdvisorError, TailoringAdvisorService
from backend.models.tailoring_analysis import TailoringAnalysis
from backend.repositories.tailoring_analysis import TailoringAnalysisRepository
from backend.repositories.tailoring import TailoringDocumentRepository
from backend.services.base import BaseService
from backend.services.tailoring_service import TailoringService

logger = logging.getLogger(__name__)


class TailoringAnalysisError(Exception):
    """Raised when the analysis pipeline cannot complete."""


class TailoringAnalysisService(BaseService[TailoringAnalysisRepository]):
    """Orchestrates JD analysis, CV-to-JD matching, and tailoring recommendations."""

    def __init__(
        self,
        repository: TailoringAnalysisRepository,
        document_service: TailoringService,
        jd_analyzer: JDAnalyzerService | None = None,
        cv_jd_matcher: CVJDMatcherService | None = None,
        tailoring_advisor: TailoringAdvisorService | None = None,
    ) -> None:
        super().__init__(repository)
        self.repository = repository
        self.document_service = document_service
        self._jd_analyzer = jd_analyzer or JDAnalyzerService()
        self._cv_jd_matcher = cv_jd_matcher or CVJDMatcherService()
        self._tailoring_advisor = tailoring_advisor or TailoringAdvisorService()

    async def run_analysis(self, user_id: uuid.UUID) -> TailoringAnalysis:
        """Run the full analysis pipeline for a user.

        Loads the user's latest CV and JD documents, then executes:
        1. JD analysis (structured extraction)
        2. CV-to-JD matching (evidence-based comparison)
        3. Tailoring recommendations (structured output for Phase 3)

        All three stages are persisted in a single :class:`TailoringAnalysis`
        record. If a stage fails, the error is recorded and earlier results
        are still persisted.

        Args:
            user_id: The authenticated user's UUID.

        Returns:
            The persisted :class:`TailoringAnalysis` record.

        Raises:
            TailoringAnalysisError: If no CV or JD document exists.
        """
        # Step 0 — load documents.
        cv_doc = await self.document_service.get_latest_cv(user_id)
        jd_doc = await self.document_service.get_latest_job_description(user_id)

        if cv_doc is None:
            raise TailoringAnalysisError(
                "No CV found. Please upload a CV before running analysis."
            )
        if jd_doc is None:
            raise TailoringAnalysisError(
                "No job description found. Please upload or paste a JD before running analysis."
            )

        cv_text = cv_doc.extracted_text or cv_doc.pasted_text or ""
        jd_text = jd_doc.extracted_text or jd_doc.pasted_text or ""

        if not cv_text.strip():
            raise TailoringAnalysisError("CV document has no extracted text.")
        if not jd_text.strip():
            raise TailoringAnalysisError("Job description has no text.")

        # Create the analysis record.
        analysis = await self.repository.create(
            user_id=user_id,
            cv_document_id=cv_doc.id,
            jd_document_id=jd_doc.id,
        )
        await self.commit()

        # Step 1 — JD analysis.
        jd_analysis_result: dict | None = None
        error_msg: str | None = None
        try:
            jd_analysis_result = await self._jd_analyzer.analyze(jd_text)
            await self.repository.update(analysis, jd_analysis=jd_analysis_result)
            await self.commit()
        except JDAnalysisError as exc:
            error_msg = f"JD analysis failed: {exc}"
            logger.exception("JD analysis failed for user %s", user_id)
            await self.repository.update(analysis, error=error_msg)
            await self.commit()
            # JD analysis is a prerequisite — stop here.
            return await self.repository.get_by(id=analysis.id) or analysis

        # Step 2 — CV-to-JD matching.
        match_result: dict | None = None
        try:
            match_result = await self._cv_jd_matcher.match(cv_text, jd_analysis_result)
            await self.repository.update(analysis, match_result=match_result)
            await self.commit()
        except CVJDMatcherError as exc:
            error_msg = f"CV-to-JD matching failed: {exc}"
            logger.exception("CV-to-JD matching failed for user %s", user_id)
            await self.repository.update(analysis, error=error_msg)
            await self.commit()
            # Matching is a prerequisite for recommendations — stop here.
            return await self.repository.get_by(id=analysis.id) or analysis

        # Step 3 — Tailoring recommendations.
        recommendations: dict | None = None
        try:
            recommendations = await self._tailoring_advisor.advise(
                cv_text, jd_analysis_result, match_result,
            )
            await self.repository.update(analysis, recommendations=recommendations)
            await self.commit()
        except TailoringAdvisorError as exc:
            error_msg = f"Tailoring recommendations failed: {exc}"
            logger.exception("Tailoring recommendations failed for user %s", user_id)
            await self.repository.update(analysis, error=error_msg)
            await self.commit()

        return await self.repository.get_by(id=analysis.id) or analysis

    async def get_latest_for_user(self, user_id: uuid.UUID) -> TailoringAnalysis | None:
        """Return the user's most recent analysis, or None."""
        return await self.repository.get_latest_for_user(user_id)

    async def get_owned_by_id(
        self,
        analysis_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> TailoringAnalysis | None:
        """Return an analysis only if it belongs to the given user."""
        return await self.repository.get_owned_by_id(analysis_id, user_id)
