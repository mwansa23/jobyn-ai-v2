"""Targeted gap tests for the CV factual-integrity remediation.

Covers only scenarios that are genuinely missing from the existing
test_cv_validator.py (56 passed) and test_tailoring_fixes.py (117 passed).

Gap 1: Application-package 422 must originate from _check_export_allowed,
        not from request-schema validation.
Gap 2: Enforcement cutoff boundary — record created at exactly
        2026-11-17T14:46:54Z (the cutoff) must be blocked.
Gap 3: _resolve_original_cv_text fails closed when analysis_id is None
        (FK set to NULL) — no silent fallback to get_latest_cv().
Gap 4: _resolve_original_cv_text fails closed when the analysis record
        is not found (deleted or orphaned).
Gap 5: Old TestEmptyOriginal assertion updated to also assert skipped=True.
"""

from __future__ import annotations

import datetime
import uuid
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.services.cv_validator import CVValidator

# ── Reuse shared fixtures and mocks from test_tailoring_fixes ────────────────

from tests.test_tailoring_fixes import (
    ANALYZE_URL,
    CV_UPLOAD_URL,
    DOCX_CT,
    GENERATE_URL,
    JD_PASTE_URL,
    MOCK_CV_WITH_ALL_SECTIONS,
    MOCK_CV_WITH_FABRICATIONS,
    MOCK_JD_ANALYSIS,
    MOCK_MATCH,
    MOCK_RECS,
    ORIGINAL_CV_TEXT,
    PATCH_JD,
    PATCH_MATCH,
    PATCH_RECS,
    PATCH_REWRITE,
    _auth,
    _make_docx,
    _register_and_login,
)


# ── Gap 1: Application-package 422 must come from the validation guard ───────

class TestApplicationPackageGuardOrigin:
    """The 422 from the application-package endpoint must originate from
    _check_export_allowed(), not from request-schema validation."""

    def _generate_invalid_cv(self, client: TestClient) -> str:
        """Generate a CV with fabricated content (is_valid=False). Return token."""
        token = _register_and_login(client)
        docx = _make_docx("Minimal text.")
        client.post(CV_UPLOAD_URL, headers=_auth(token),
                    files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_FABRICATIONS)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        assert r.status_code == 201
        return token

    def test_invalid_cv_detail_mentions_unsupported_claims(self, client: TestClient):
        """The 422 detail must mention 'unsupported claims', proving it came
        from _check_export_allowed (is_valid=False path), not from request
        schema validation (which would mention field errors)."""
        token = self._generate_invalid_cv(client)
        r = client.post(
            "/api/v1/tailoring/application-package/export",
            headers=_auth(token),
            data={"package_type": "cover_letter_cv"},
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        # Schema validation errors produce a list; the guard produces a string.
        assert isinstance(detail, str), (
            f"Expected detail to be a string from the validation guard, "
            f"got {type(detail).__name__}: {detail!r}"
        )
        assert "unsupported claims" in detail.lower(), (
            f"422 must mention 'unsupported claims' (from guard), got: {detail!r}"
        )

    def test_skipped_cv_detail_mentions_interrupted(self, client: TestClient):
        """The 422 for a skipped-validation CV must mention 'interrupted',
        proving it came from the skipped branch of _check_export_allowed."""
        token = _register_and_login(client)
        docx = _make_docx(ORIGINAL_CV_TEXT)
        client.post(CV_UPLOAD_URL, headers=_auth(token),
                    files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            with patch("backend.services.cv_validator.CVValidator.validate",
                       side_effect=RuntimeError("forced crash")):
                r = client.post(GENERATE_URL, headers=_auth(token), json={})
        assert r.status_code == 201

        r_pkg = client.post(
            "/api/v1/tailoring/application-package/export",
            headers=_auth(token),
            data={"package_type": "cover_letter_cv"},
        )
        assert r_pkg.status_code == 422
        detail = r_pkg.json()["detail"]
        assert isinstance(detail, str), f"Expected str, got {type(detail).__name__}: {detail!r}"
        assert "interrupted" in detail.lower(), (
            f"422 must mention 'interrupted' (from skipped-validation guard path), "
            f"got: {detail!r}"
        )


# ── Gap 2: Enforcement cutoff boundary ───────────────────────────────────────

class TestEnforcementCutoffBoundary:
    """Records at exactly the enforcement cutoff must be blocked (not allowed)."""

    def test_record_at_exactly_cutoff_is_blocked(self):
        """created_at == 2026-11-17T14:46:54Z (the exact enforcement cutoff)
        must trigger the post-enforcement block, not the legacy pass-through."""
        from backend.api.routes.tailoring import _check_export_allowed
        import fastapi

        cv = MagicMock()
        cv.content = {}  # no validation key
        cv.id = uuid.uuid4()
        # Exactly at the cutoff — must be blocked (>= comparison).
        cv.created_at = datetime.datetime(2026, 11, 17, 14, 46, 54,
                                          tzinfo=datetime.timezone.utc)

        with pytest.raises(fastapi.HTTPException) as exc_info:
            _check_export_allowed(cv)
        assert exc_info.value.status_code == 422
        assert "no validation record" in exc_info.value.detail.lower()

    def test_record_one_second_before_cutoff_is_allowed(self):
        """created_at == 2026-11-17T14:46:53Z (one second before the cutoff)
        must pass as a genuine legacy record."""
        from backend.api.routes.tailoring import _check_export_allowed

        cv = MagicMock()
        cv.content = {}  # no validation key
        cv.id = uuid.uuid4()
        # One second before cutoff — legacy, must not raise.
        cv.created_at = datetime.datetime(2026, 11, 17, 14, 46, 53,
                                          tzinfo=datetime.timezone.utc)
        _check_export_allowed(cv)  # must not raise


# ── Gap 3 & 4: _resolve_original_cv_text fails closed ────────────────────────

class TestResolveOriginalCvTextFailsClosed:
    """_resolve_original_cv_text must return "" (not substitute another CV)
    when the analysis chain is broken."""

    @pytest.mark.asyncio
    async def test_none_analysis_id_returns_empty_string(self):
        """cv_record.analysis_id is None → empty string (no fallback to
        get_latest_cv)."""
        from backend.services.tailoring_generation_service import TailoringGenerationService

        cv_record = MagicMock()
        cv_record.analysis_id = None

        svc = TailoringGenerationService.__new__(TailoringGenerationService)
        svc.repository = MagicMock()
        svc._analysis_repository = MagicMock()
        svc.document_service = MagicMock()

        result = await svc._resolve_original_cv_text(uuid.uuid4(), cv_record)
        assert result == "", (
            "analysis_id=None must return empty string, not fall back to "
            f"get_latest_cv(). Got: {result!r}"
        )
        # get_latest_cv must NOT have been called.
        svc.document_service.get_latest_cv.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_analysis_record_returns_empty_string(self):
        """analysis_id is set but analysis_repo.get_by() returns None
        (deleted/orphaned analysis) → empty string, not get_latest_cv."""
        from backend.services.tailoring_generation_service import TailoringGenerationService

        cv_record = MagicMock()
        cv_record.analysis_id = uuid.uuid4()

        svc = TailoringGenerationService.__new__(TailoringGenerationService)
        svc.repository = MagicMock()
        svc._analysis_repository = MagicMock()
        svc.document_service = MagicMock()

        # Repo returns None — analysis was deleted.
        analysis_repo_mock = AsyncMock()
        analysis_repo_mock.get_by = AsyncMock(return_value=None)
        svc._get_analysis_repo = MagicMock(return_value=analysis_repo_mock)

        result = await svc._resolve_original_cv_text(uuid.uuid4(), cv_record)
        assert result == "", (
            "Missing analysis record must return empty string, not fall back "
            f"to get_latest_cv(). Got: {result!r}"
        )
        svc.document_service.get_latest_cv.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_document_returns_empty_string(self):
        """Document linked by cv_document_id cannot be found (deleted, or
        ownership mismatch) → empty string, not get_latest_cv."""
        from backend.services.tailoring_generation_service import TailoringGenerationService

        analysis_id = uuid.uuid4()
        cv_record = MagicMock()
        cv_record.analysis_id = analysis_id

        analysis_mock = MagicMock()
        analysis_mock.cv_document_id = uuid.uuid4()

        analysis_repo_mock = AsyncMock()
        analysis_repo_mock.get_by = AsyncMock(return_value=analysis_mock)

        svc = TailoringGenerationService.__new__(TailoringGenerationService)
        svc.repository = MagicMock()
        svc._analysis_repository = MagicMock()
        svc._get_analysis_repo = MagicMock(return_value=analysis_repo_mock)

        # Document not found (ownership check fails or doc deleted).
        doc_svc_mock = AsyncMock()
        doc_svc_mock.get_document_for_user = AsyncMock(return_value=None)
        svc.document_service = doc_svc_mock

        result = await svc._resolve_original_cv_text(uuid.uuid4(), cv_record)
        assert result == "", (
            "Missing cv_document must return empty string, not fall back "
            f"to get_latest_cv(). Got: {result!r}"
        )
        doc_svc_mock.get_latest_cv.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_extracted_text_returns_empty_string(self):
        """Document exists but extracted_text and pasted_text are both empty
        → empty string, not get_latest_cv."""
        from backend.services.tailoring_generation_service import TailoringGenerationService

        analysis_id = uuid.uuid4()
        cv_record = MagicMock()
        cv_record.analysis_id = analysis_id

        analysis_mock = MagicMock()
        analysis_mock.cv_document_id = uuid.uuid4()

        analysis_repo_mock = AsyncMock()
        analysis_repo_mock.get_by = AsyncMock(return_value=analysis_mock)

        doc_mock = MagicMock()
        doc_mock.extracted_text = "   "   # whitespace only
        doc_mock.pasted_text = ""

        doc_svc_mock = AsyncMock()
        doc_svc_mock.get_document_for_user = AsyncMock(return_value=doc_mock)

        svc = TailoringGenerationService.__new__(TailoringGenerationService)
        svc.repository = MagicMock()
        svc._analysis_repository = MagicMock()
        svc._get_analysis_repo = MagicMock(return_value=analysis_repo_mock)
        svc.document_service = doc_svc_mock

        result = await svc._resolve_original_cv_text(uuid.uuid4(), cv_record)
        assert result == "", (
            "Whitespace-only extracted_text must return empty string, "
            f"not fall back to get_latest_cv(). Got: {result!r}"
        )
        doc_svc_mock.get_latest_cv.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_chain_returns_correct_text(self):
        """Happy path: valid analysis → valid document → non-empty text →
        returned directly."""
        from backend.services.tailoring_generation_service import TailoringGenerationService

        analysis_id = uuid.uuid4()
        cv_record = MagicMock()
        cv_record.analysis_id = analysis_id

        analysis_mock = MagicMock()
        analysis_mock.cv_document_id = uuid.uuid4()

        analysis_repo_mock = AsyncMock()
        analysis_repo_mock.get_by = AsyncMock(return_value=analysis_mock)

        doc_mock = MagicMock()
        doc_mock.extracted_text = "Jane Smith\nHR Manager at React Digital Consulting (2019-Present)"
        doc_mock.pasted_text = None

        doc_svc_mock = AsyncMock()
        doc_svc_mock.get_document_for_user = AsyncMock(return_value=doc_mock)

        svc = TailoringGenerationService.__new__(TailoringGenerationService)
        svc.repository = MagicMock()
        svc._analysis_repository = MagicMock()
        svc._get_analysis_repo = MagicMock(return_value=analysis_repo_mock)
        svc.document_service = doc_svc_mock

        result = await svc._resolve_original_cv_text(uuid.uuid4(), cv_record)
        assert "React Digital Consulting" in result
        doc_svc_mock.get_latest_cv.assert_not_called()


# ── Gap 5: TestEmptyOriginal's assertion updated for skipped=True ─────────────

class TestEmptyOriginalSkippedField:
    """The existing TestEmptyOriginal test asserts is_valid=True but does not
    check skipped=True.  This gap test verifies the skipped flag specifically."""

    def test_empty_original_produces_skipped_true_not_false(self):
        """Empty source must yield skipped=True, not skipped=False.
        A skipped=False result with is_valid=True would pass the export guard
        silently; skipped=True correctly blocks export."""
        sections = [{"heading": "Experience", "type": "experience", "items": [
            {"title": "Developer", "company": "AnyCompany", "dates": "2020-2024",
             "details": ["Did things"]},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, "")
        assert result.is_valid is True        # unchanged
        assert result.skipped is True         # must be True, not False
        d = result.to_dict()
        assert d["skipped"] is True           # must round-trip through to_dict()
