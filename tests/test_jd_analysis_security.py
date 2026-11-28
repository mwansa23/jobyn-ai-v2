"""Security and backward-compatibility tests for JD-to-analysis integrity.

Covers gaps not addressed in test_jd_analysis_integrity.py or
test_tailoring_phase2.py:

1. Ownership enforcement — explicit document IDs must be verified as belonging
   to the authenticated user; a foreign ID must never trigger fallback.
2. Invalid ID handling — nonexistent or wrong-owner IDs must return 422, not
   500 or silently fall through to another document.
3. Backward compatibility — requests without document IDs (empty payload or
   null) must continue to work using the latest documents.
4. Session restoration — restoring a session with a saved JD document ID must
   use that document, not the most recently uploaded one.
5. No fallback on invalid explicit ID — if the specified JD is invalid, the
   endpoint must fail with a clear error; it must NOT silently substitute the
   latest JD or another user's JD.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, patch

from docx import Document
from fastapi.testclient import TestClient

# ── Constants ─────────────────────────────────────────────────────────────────

REGISTER_URL   = "/api/v1/auth/register"
LOGIN_URL      = "/api/v1/auth/login"
CV_UPLOAD_URL  = "/api/v1/tailoring/cv/upload"
JD_PASTE_URL   = "/api/v1/tailoring/job-description/paste"
ANALYZE_URL    = "/api/v1/tailoring/analyze"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

PATCH_JD    = "backend.ai.jd_analyzer.JDAnalyzerService.analyze"
PATCH_MATCH = "backend.ai.cv_jd_matcher.CVJDMatcherService.match"
PATCH_RECS  = "backend.ai.tailoring_advisor.TailoringAdvisorService.advise"

_MOCK_JD = {
    "job_title": "HR Manager", "employer": "Acme Corp", "location": "Lusaka",
    "employment_type": "full-time", "responsibilities": ["Lead HR"],
    "essential_qualifications": ["Degree"], "desirable_qualifications": [],
    "required_experience_years": "3", "technical_skills": ["HRIS"],
    "soft_skills": ["communication"], "industry_keywords": ["HR"],
    "application_requirements": [], "summary": "HR Manager at Acme Corp.",
}
_MOCK_MATCH = {
    "matching_skills": [], "matching_experience": [], "matching_achievements": [],
    "missing_requirements": [], "missing_keywords": [], "weak_sections": [],
    "overall_match_summary": "Good match.",
}
_MOCK_RECS = {
    "section_recommendations": [], "achievements_to_highlight": [],
    "information_to_request": [], "keyword_optimisation": [],
    "priority_actions": [], "tailoring_summary": "Good.",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_docx(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO(); doc.save(buf); return buf.getvalue()

CV_TEXT = "Jane Smith HR Professional. HR Manager at ABC Co 2018-2023. HRIS, Recruitment."

def _register_and_login(client: TestClient) -> str:
    email = f"sec_{uuid.uuid4().hex[:8]}@example.com"
    pw = "Security123!"
    client.post(REGISTER_URL, json={"email": email, "password": pw})
    r = client.post(LOGIN_URL, json={"email": email, "password": pw})
    return r.json()["access_token"]

def _auth(token: str):
    return {"Authorization": f"Bearer {token}"}

def _upload_cv(client: TestClient, token: str) -> str:
    r = client.post(CV_UPLOAD_URL, headers=_auth(token),
                    files={"file": ("cv.docx", _make_docx(CV_TEXT), DOCX_CT)})
    assert r.status_code == 201, r.text
    return r.json()["id"]

def _paste_jd(client: TestClient, token: str, text: str = "HR Manager job.") -> str:
    r = client.post(JD_PASTE_URL, headers=_auth(token), json={"text": text})
    assert r.status_code == 201, r.text
    return r.json()["id"]

def _analyze(client: TestClient, token: str, payload: dict | None = None) -> dict:
    with (
        patch(PATCH_JD,    new=AsyncMock(return_value=_MOCK_JD)),
        patch(PATCH_MATCH, new=AsyncMock(return_value=_MOCK_MATCH)),
        patch(PATCH_RECS,  new=AsyncMock(return_value=_MOCK_RECS)),
    ):
        r = client.post(ANALYZE_URL, headers=_auth(token), json=payload or {})
    return r


# ── 1. Backward compatibility ─────────────────────────────────────────────────

class TestBackwardCompatibility:
    """Requests without document IDs must continue to work exactly as before."""

    def test_empty_payload_uses_latest_documents(self, client: TestClient) -> None:
        """json={} (no document IDs) must still produce a valid analysis."""
        token = _register_and_login(client)
        _upload_cv(client, token)
        jd_id = _paste_jd(client, token)

        r = _analyze(client, token, {})
        assert r.status_code == 201, f"Empty payload must succeed: {r.text}"
        data = r.json()
        assert data["cv_document_id"] is not None
        assert data["jd_document_id"] == jd_id
        assert data["error"] is None

    def test_null_payload_uses_latest_documents(self, client: TestClient) -> None:
        """No body at all (None payload) must also work."""
        token = _register_and_login(client)
        _upload_cv(client, token)
        _paste_jd(client, token)

        with (
            patch(PATCH_JD,    new=AsyncMock(return_value=_MOCK_JD)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=_MOCK_MATCH)),
            patch(PATCH_RECS,  new=AsyncMock(return_value=_MOCK_RECS)),
        ):
            r = client.post(ANALYZE_URL, headers=_auth(token))  # no json body at all
        assert r.status_code == 201, f"Null body must succeed: {r.text}"

    def test_explicit_ids_in_payload_are_used(self, client: TestClient) -> None:
        """Explicit document IDs in payload must be honoured."""
        token = _register_and_login(client)
        cv_id = _upload_cv(client, token)
        jd_id = _paste_jd(client, token, "Specific HR role.")

        r = _analyze(client, token, {"cv_document_id": cv_id, "jd_document_id": jd_id})
        assert r.status_code == 201
        data = r.json()
        assert data["cv_document_id"] == cv_id
        assert data["jd_document_id"] == jd_id


# ── 2. Ownership enforcement ──────────────────────────────────────────────────

class TestOwnershipEnforcement:
    """Explicit document IDs must be verified as belonging to the requesting user."""

    def test_foreign_jd_id_returns_422_not_fallback(self, client: TestClient) -> None:
        """Supplying another user's JD document ID must return 422, not use
        the latest JD or the other user's JD silently."""
        token_a = _register_and_login(client)
        token_b = _register_and_login(client)

        # User A uploads CV and pastes JD
        _upload_cv(client, token_a)
        jd_a_id = _paste_jd(client, token_a, "User A job description.")

        # User B uploads CV and pastes their own JD
        _upload_cv(client, token_b)
        _paste_jd(client, token_b, "User B job description.")

        # User B tries to analyze using User A's JD document ID
        r = _analyze(client, token_b, {"jd_document_id": jd_a_id})

        assert r.status_code == 422, (
            f"Using another user's JD document ID must return 422, "
            f"not silently substitute or succeed. Got {r.status_code}: {r.text}"
        )
        detail = r.json()["detail"].lower()
        assert "not found" in detail or "belong" in detail or "document" in detail, (
            f"422 detail should explain the ownership failure: {r.json()['detail']}"
        )

    def test_foreign_cv_id_returns_422(self, client: TestClient) -> None:
        """Supplying another user's CV document ID must return 422."""
        token_a = _register_and_login(client)
        token_b = _register_and_login(client)

        # User A uploads a CV
        cv_a_id = _upload_cv(client, token_a)

        # User B uploads their own CV and JD
        _upload_cv(client, token_b)
        _paste_jd(client, token_b, "User B job.")

        # User B tries to analyze using User A's CV
        r = _analyze(client, token_b, {"cv_document_id": cv_a_id})

        assert r.status_code == 422, (
            f"Using another user's CV document ID must return 422. "
            f"Got {r.status_code}: {r.text}"
        )

    def test_nonexistent_jd_id_returns_422(self, client: TestClient) -> None:
        """Supplying a UUID that does not exist in the database must return 422."""
        token = _register_and_login(client)
        _upload_cv(client, token)
        _paste_jd(client, token)  # valid JD exists, but we won't use its ID

        fake_id = str(uuid.uuid4())
        r = _analyze(client, token, {"jd_document_id": fake_id})

        assert r.status_code == 422, (
            f"Nonexistent JD document ID must return 422, "
            f"not fall through to the latest JD. Got {r.status_code}: {r.text}"
        )

    def test_nonexistent_cv_id_returns_422(self, client: TestClient) -> None:
        """Supplying a nonexistent CV document UUID must return 422."""
        token = _register_and_login(client)
        _upload_cv(client, token)
        _paste_jd(client, token)

        fake_id = str(uuid.uuid4())
        r = _analyze(client, token, {"cv_document_id": fake_id})

        assert r.status_code == 422, (
            f"Nonexistent CV document ID must return 422. Got {r.status_code}: {r.text}"
        )

    def test_invalid_id_does_not_fall_back_to_latest(self, client: TestClient) -> None:
        """When an invalid JD ID is supplied, the backend must NOT silently
        substitute the latest JD.  It must fail with 422."""
        token = _register_and_login(client)
        _upload_cv(client, token)
        _paste_jd(client, token, "Latest JD that must NOT be used as fallback.")

        fake_id = str(uuid.uuid4())
        r = _analyze(client, token, {"jd_document_id": fake_id})

        # Must be 422, not 201 (which would mean it fell back to the real latest JD)
        assert r.status_code == 422, (
            "An invalid explicit JD ID must NEVER cause silent fallback to "
            f"the latest JD. Got {r.status_code}: {r.text}"
        )

    def test_both_ids_must_pass_ownership(self, client: TestClient) -> None:
        """Both CV and JD document IDs must pass ownership; one foreign ID blocks all."""
        token_a = _register_and_login(client)
        token_b = _register_and_login(client)

        cv_a_id = _upload_cv(client, token_a)
        jd_a_id = _paste_jd(client, token_a)

        # User B uploads their own docs
        cv_b_id = _upload_cv(client, token_b)
        _paste_jd(client, token_b)

        # User B supplies own CV but other user's JD
        r = _analyze(client, token_b,
                     {"cv_document_id": cv_b_id, "jd_document_id": jd_a_id})
        assert r.status_code == 422, (
            "Foreign JD ID must block analysis even when CV ID is valid."
        )

        # User B supplies own JD but other user's CV
        jd_b_id = _paste_jd(client, token_b, "User B new JD.")
        r2 = _analyze(client, token_b,
                      {"cv_document_id": cv_a_id, "jd_document_id": jd_b_id})
        assert r2.status_code == 422, (
            "Foreign CV ID must block analysis even when JD ID is valid."
        )


# ── 3. Session restoration ────────────────────────────────────────────────────

class TestSessionRestoration:
    """Analysing with a specific saved document ID (session restore) must use
    exactly that document, not whatever was most recently uploaded."""

    def test_explicit_older_jd_id_is_used_not_latest(self, client: TestClient) -> None:
        """When the frontend restores a session with a saved JD document ID,
        that specific document must be used even if a newer JD was uploaded
        later."""
        token = _register_and_login(client)
        cv_id = _upload_cv(client, token)

        # Paste the original session JD (older)
        saved_jd_id = _paste_jd(client, token, "Original HR Manager role — the saved session JD.")

        # User then pastes a different JD (newer — this would be 'latest')
        _paste_jd(client, token, "Completely different new JD pasted later.")

        # Session restore: analyse explicitly with the saved JD ID
        r = _analyze(client, token, {"cv_document_id": cv_id, "jd_document_id": saved_jd_id})
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["jd_document_id"] == saved_jd_id, (
            "Session restore with explicit saved JD ID must use that document, "
            "not the most recently pasted JD."
        )
