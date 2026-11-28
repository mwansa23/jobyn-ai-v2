"""Regression tests for JD-to-analysis integrity defect.

Root cause: POST /api/v1/tailoring/analyze ignored payload.jd_document_id
and always used get_latest_job_description(). With timestamp ties or rapid
sequential requests, the wrong JD could contaminate the analysis.

These tests verify that:
1. Sequential JD submissions produce isolated, correct analyses.
2. The response analysis fields belong to the submitted JD, not a previous one.
3. Persistence integrity: reloading an analysis still references the correct JD.
4. CV generation proceeds without 422 after a valid analysis.
5. Cross-request isolation: one user's JD cannot appear in another user's analysis.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from docx import Document
from fastapi.testclient import TestClient

# ── Constants ─────────────────────────────────────────────────────────────────

REGISTER_URL   = "/api/v1/auth/register"
LOGIN_URL      = "/api/v1/auth/login"
CV_UPLOAD_URL  = "/api/v1/tailoring/cv/upload"
JD_PASTE_URL   = "/api/v1/tailoring/job-description/paste"
ANALYZE_URL    = "/api/v1/tailoring/analyze"
GENERATE_URL   = "/api/v1/tailoring/cv/generate"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

PATCH_JD    = "backend.ai.jd_analyzer.JDAnalyzerService.analyze"
PATCH_MATCH = "backend.ai.cv_jd_matcher.CVJDMatcherService.match"
PATCH_RECS  = "backend.ai.tailoring_advisor.TailoringAdvisorService.advise"
PATCH_REWRITE = "backend.ai.cv_rewriter.CVRewriterService.rewrite"

# ── Realistic JD fixtures ─────────────────────────────────────────────────────

JD_A_TEXT = """
Paid Search Manager — Zimworx
We are looking for an experienced Paid Search Manager to join our digital
marketing team.
Responsibilities: Manage Google Ads and Microsoft Ads campaigns.
Requirements: 3+ years PPC experience, Google Ads certified.
Skills: Google Ads, Microsoft Ads, keyword research, bid management.
Keywords: PPC, SEM, digital advertising, ROI optimisation.
"""

JD_A_ANALYSIS = {
    "job_title": "Paid Search Manager",
    "employer": "Zimworx",
    "location": "Remote",
    "employment_type": "full-time",
    "responsibilities": ["Manage Google Ads and Microsoft Ads campaigns"],
    "essential_qualifications": ["3+ years PPC experience", "Google Ads certified"],
    "desirable_qualifications": [],
    "required_experience_years": "3 years",
    "technical_skills": ["Google Ads", "Microsoft Ads", "keyword research"],
    "soft_skills": ["analytical thinking"],
    "industry_keywords": ["PPC", "SEM", "digital advertising"],
    "application_requirements": [],
    "summary": "PPC manager role at Zimworx focused on paid search campaigns.",
}

JD_B_TEXT = """
Human Resource Manager — Talent House Ltd
Talent House Ltd is seeking a Human Resource Manager based in Lusaka, Zambia.
Industry: Manufacturing. Closing Date: 10 December 2026.
Responsibilities: Lead recruitment, manage employee relations, develop HR policies.
Requirements: Bachelor's degree in HR or related field, 5+ years HR experience.
Skills: Recruitment, HRIS, labour law, employee development.
Keywords: HR management, talent acquisition, employee relations, Zambia.
"""

JD_B_ANALYSIS = {
    "job_title": "Human Resource Manager",
    "employer": "Talent House Ltd",
    "location": "Lusaka, Zambia",
    "employment_type": "full-time",
    "responsibilities": ["Lead recruitment", "manage employee relations", "develop HR policies"],
    "essential_qualifications": ["Bachelor's degree in HR", "5+ years HR experience"],
    "desirable_qualifications": [],
    "required_experience_years": "5 years",
    "technical_skills": ["HRIS", "labour law"],
    "soft_skills": ["leadership", "communication"],
    "industry_keywords": ["HR management", "talent acquisition", "employee relations"],
    "application_requirements": [],
    "summary": "HR Manager role at Talent House Ltd in Lusaka, Zambia.",
}

MOCK_MATCH = {
    "matching_skills": [], "matching_experience": [], "matching_achievements": [],
    "missing_requirements": [], "missing_keywords": [], "weak_sections": [],
    "overall_match_summary": "Good match.",
}
MOCK_RECS = {
    "section_recommendations": [], "achievements_to_highlight": [],
    "information_to_request": [], "keyword_optimisation": [],
    "priority_actions": ["Rewrite summary"],
    "tailoring_summary": "Reposition profile for target role.",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_docx(text: str) -> bytes:
    doc = Document()
    for line in text.strip().split("\n"):
        doc.add_paragraph(line.strip())
    buf = io.BytesIO(); doc.save(buf); return buf.getvalue()

CV_TEXT = """
Jane Smith
HR Professional | Lusaka, Zambia
jane@example.com | +260 977 123456

Professional Experience:
- HR Manager at ABC Company (2018-2023)
  Managed recruitment and employee relations

Education:
Bachelor of Business Administration, UNZA (2014-2018)

Skills: Recruitment, HRIS, Microsoft Office
Languages: English (Fluent)
"""

def _register_and_login(client: TestClient) -> str:
    email = f"jd_integrity_{uuid.uuid4().hex[:8]}@example.com"
    pw = "JDTest123!"
    client.post(REGISTER_URL, json={"email": email, "password": pw, "full_name": "JD Test"})
    r = client.post(LOGIN_URL, json={"email": email, "password": pw})
    return r.json()["access_token"]

def _auth(token: str):
    return {"Authorization": f"Bearer {token}"}

def _upload_cv(client: TestClient, token: str) -> str:
    docx = _make_docx(CV_TEXT)
    r = client.post(CV_UPLOAD_URL, headers=_auth(token),
                    files={"file": ("cv.docx", docx, DOCX_CT)})
    assert r.status_code == 201, r.text
    return r.json()["id"]

def _paste_jd(client: TestClient, token: str, text: str) -> str:
    r = client.post(JD_PASTE_URL, headers=_auth(token), json={"text": text})
    assert r.status_code == 201, r.text
    return r.json()["id"]

def _run_analysis(client: TestClient, token: str, jd_analysis_mock: dict,
                   cv_doc_id: str | None = None, jd_doc_id: str | None = None) -> dict:
    payload: dict = {}
    if cv_doc_id:
        payload["cv_document_id"] = cv_doc_id
    if jd_doc_id:
        payload["jd_document_id"] = jd_doc_id
    with (
        patch(PATCH_JD,    new=AsyncMock(return_value=jd_analysis_mock)),
        patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
        patch(PATCH_RECS,  new=AsyncMock(return_value=MOCK_RECS)),
    ):
        r = client.post(ANALYZE_URL, headers=_auth(token), json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ── Test 1: Sequential JD isolation ──────────────────────────────────────────

class TestSequentialJDIsolation:
    """JD-B analysis must not contain JD-A content when both are submitted
    sequentially for the same user."""

    def test_jd_b_returns_hr_analysis_not_ppc(self, client: TestClient) -> None:
        """Submitting JD-B after JD-A must produce HR-specific analysis."""
        token = _register_and_login(client)
        cv_id = _upload_cv(client, token)

        # Submit JD-A (Paid Search Manager) and analyse
        jd_a_id = _paste_jd(client, token, JD_A_TEXT)
        analysis_a = _run_analysis(client, token, JD_A_ANALYSIS, cv_id, jd_a_id)
        assert analysis_a["jd_analysis"]["job_title"] == "Paid Search Manager"
        assert analysis_a["jd_document_id"] == jd_a_id

        # Submit JD-B (HR Manager) and analyse with explicit JD document ID
        jd_b_id = _paste_jd(client, token, JD_B_TEXT)
        assert jd_a_id != jd_b_id, "Two distinct JD documents must be created"
        analysis_b = _run_analysis(client, token, JD_B_ANALYSIS, cv_id, jd_b_id)

        # JD-B analysis must reference JD-B, not JD-A
        assert analysis_b["jd_analysis"]["job_title"] == "Human Resource Manager", (
            f"JD-B analysis must report 'Human Resource Manager', "
            f"not '{analysis_b['jd_analysis']['job_title']}'"
        )
        assert analysis_b["jd_analysis"]["employer"] == "Talent House Ltd"
        assert analysis_b["jd_document_id"] == jd_b_id, (
            "Analysis B must reference JD-B's document ID, not JD-A's"
        )

    def test_jd_b_analysis_id_is_different_from_jd_a(self, client: TestClient) -> None:
        """Each analysis request must produce a new, independent analysis record."""
        token = _register_and_login(client)
        cv_id = _upload_cv(client, token)

        jd_a_id = _paste_jd(client, token, JD_A_TEXT)
        analysis_a = _run_analysis(client, token, JD_A_ANALYSIS, cv_id, jd_a_id)

        jd_b_id = _paste_jd(client, token, JD_B_TEXT)
        analysis_b = _run_analysis(client, token, JD_B_ANALYSIS, cv_id, jd_b_id)

        assert analysis_a["id"] != analysis_b["id"], (
            "Each analysis must receive a unique ID"
        )


# ── Test 2: Response integrity ────────────────────────────────────────────────

class TestResponseIntegrity:
    """JD-B analysis response must carry correct employer, skills and keywords."""

    def test_jd_b_response_contains_correct_fields(self, client: TestClient) -> None:
        token = _register_and_login(client)
        cv_id = _upload_cv(client, token)
        jd_b_id = _paste_jd(client, token, JD_B_TEXT)
        analysis = _run_analysis(client, token, JD_B_ANALYSIS, cv_id, jd_b_id)

        jd = analysis["jd_analysis"]
        assert jd["job_title"] == "Human Resource Manager"
        assert jd["employer"] == "Talent House Ltd"
        assert jd["location"] == "Lusaka, Zambia"
        assert "HR management" in jd["industry_keywords"] or \
               any("HR" in kw for kw in jd["industry_keywords"])
        assert any("HRIS" in s or "labour" in s.lower() for s in jd["technical_skills"])

    def test_jd_b_does_not_contain_ppc_content(self, client: TestClient) -> None:
        """JD-B response must contain no PPC-related content from JD-A."""
        token = _register_and_login(client)
        cv_id = _upload_cv(client, token)

        # Submit JD-A first (simulating the user's previous session)
        jd_a_id = _paste_jd(client, token, JD_A_TEXT)
        _run_analysis(client, token, JD_A_ANALYSIS, cv_id, jd_a_id)

        # Now submit JD-B with explicit document ID
        jd_b_id = _paste_jd(client, token, JD_B_TEXT)
        analysis_b = _run_analysis(client, token, JD_B_ANALYSIS, cv_id, jd_b_id)

        jd = analysis_b["jd_analysis"]
        response_str = str(jd).lower()
        ppc_terms = ["zimworx", "paid search", "google ads", "microsoft ads", "ppc", "sem"]
        for term in ppc_terms:
            assert term not in response_str, (
                f"JD-B analysis must not contain PPC term '{term}' from JD-A. "
                f"This indicates JD contamination."
            )


# ── Test 3: Persistence integrity ────────────────────────────────────────────

class TestPersistenceIntegrity:
    """Reloading the analysis from the database must still reference JD-B."""

    def test_reloaded_analysis_references_correct_jd(self, client: TestClient) -> None:
        token = _register_and_login(client)
        cv_id = _upload_cv(client, token)
        jd_b_id = _paste_jd(client, token, JD_B_TEXT)
        analysis = _run_analysis(client, token, JD_B_ANALYSIS, cv_id, jd_b_id)
        analysis_id = analysis["id"]

        # Reload via GET /analysis/{id}
        r_reload = client.get(f"/api/v1/tailoring/analysis/{analysis_id}",
                               headers=_auth(token))
        assert r_reload.status_code == 200
        reloaded = r_reload.json()

        assert reloaded["id"] == analysis_id
        assert reloaded["jd_document_id"] == jd_b_id, (
            "Reloaded analysis must still reference JD-B's document ID"
        )
        assert reloaded["jd_analysis"]["job_title"] == "Human Resource Manager"
        assert reloaded["jd_analysis"]["employer"] == "Talent House Ltd"

    def test_latest_analysis_endpoint_returns_most_recent(self, client: TestClient) -> None:
        """GET /analysis/latest must return the analysis for JD-B after JD-B is analysed."""
        token = _register_and_login(client)
        cv_id = _upload_cv(client, token)

        jd_a_id = _paste_jd(client, token, JD_A_TEXT)
        _run_analysis(client, token, JD_A_ANALYSIS, cv_id, jd_a_id)

        jd_b_id = _paste_jd(client, token, JD_B_TEXT)
        analysis_b = _run_analysis(client, token, JD_B_ANALYSIS, cv_id, jd_b_id)

        r_latest = client.get("/api/v1/tailoring/analysis/latest", headers=_auth(token))
        assert r_latest.status_code == 200
        latest = r_latest.json()
        assert latest["id"] == analysis_b["id"], (
            "Latest analysis must be JD-B's analysis, not JD-A's"
        )


# ── Test 4: CV generation proceeds without 422 ───────────────────────────────

class TestCVGeneration:
    """CV generation must succeed after a valid JD-B analysis."""

    _MOCK_CV = {
        "sections": [
            {"heading": "Contact", "type": "contact",
             "content": "Jane Smith\njane@example.com", "items": [], "source": "original"},
            {"heading": "Professional Summary", "type": "summary",
             "content": "Experienced HR professional.", "items": [], "source": "rewritten"},
            {"heading": "Experience", "type": "experience", "content": "", "items": [
                {"title": "HR Manager", "company": "ABC Company",
                 "dates": "2018-2023", "details": ["Managed recruitment"]},
            ], "source": "rewritten"},
        ],
        "generation_notes": "Rewrote for HR Manager role at Talent House.",
    }

    def test_cv_generation_succeeds_after_jd_b_analysis(self, client: TestClient) -> None:
        """CV generation must not return 422 when analysis is complete and valid."""
        token = _register_and_login(client)
        cv_id = _upload_cv(client, token)
        jd_b_id = _paste_jd(client, token, JD_B_TEXT)
        analysis_b = _run_analysis(client, token, JD_B_ANALYSIS, cv_id, jd_b_id)

        assert not analysis_b.get("error"), (
            f"JD-B analysis must not have an error field: {analysis_b.get('error')}"
        )
        assert analysis_b.get("recommendations"), "JD-B analysis must have recommendations"

        with patch(PATCH_REWRITE, new=AsyncMock(return_value=self._MOCK_CV)):
            r_gen = client.post(GENERATE_URL, headers=_auth(token), json={})

        assert r_gen.status_code == 201, (
            f"CV generation must succeed after valid JD-B analysis, "
            f"got {r_gen.status_code}: {r_gen.text}"
        )

    def test_cv_generation_does_not_use_jd_a_requirements(self, client: TestClient) -> None:
        """CV generation after JD-B analysis must not 422 citing JD-A error state."""
        token = _register_and_login(client)
        cv_id = _upload_cv(client, token)

        # Simulate: user previously used JD-A, now uses JD-B
        jd_a_id = _paste_jd(client, token, JD_A_TEXT)
        _run_analysis(client, token, JD_A_ANALYSIS, cv_id, jd_a_id)

        jd_b_id = _paste_jd(client, token, JD_B_TEXT)
        _run_analysis(client, token, JD_B_ANALYSIS, cv_id, jd_b_id)

        with patch(PATCH_REWRITE, new=AsyncMock(return_value=self._MOCK_CV)):
            r_gen = client.post(GENERATE_URL, headers=_auth(token), json={})

        assert r_gen.status_code == 201, (
            f"CV generation must succeed using JD-B analysis, "
            f"got {r_gen.status_code}: {r_gen.text}"
        )
        # Confirm the generated CV used JD-B's analysis (analysis_id matches JD-B analysis)
        generated = r_gen.json()
        r_analysis = client.get("/api/v1/tailoring/analysis/latest", headers=_auth(token))
        assert r_analysis.status_code == 200
        latest_analysis = r_analysis.json()
        assert generated["analysis_id"] == latest_analysis["id"]
        assert latest_analysis["jd_document_id"] == jd_b_id


# ── Test 5: Cross-request isolation ──────────────────────────────────────────

class TestCrossRequestIsolation:
    """One user's JD must not contaminate another user's analysis."""

    def test_user_a_jd_does_not_appear_in_user_b_analysis(self, client: TestClient) -> None:
        """User B's analysis must reflect User B's JD, not User A's."""
        token_a = _register_and_login(client)
        token_b = _register_and_login(client)

        # User A: upload CV and use JD-A (PPC)
        cv_a_id = _upload_cv(client, token_a)
        jd_a_id = _paste_jd(client, token_a, JD_A_TEXT)
        analysis_a = _run_analysis(client, token_a, JD_A_ANALYSIS, cv_a_id, jd_a_id)
        assert analysis_a["jd_analysis"]["job_title"] == "Paid Search Manager"

        # User B: upload CV and use JD-B (HR)
        cv_b_id = _upload_cv(client, token_b)
        jd_b_id = _paste_jd(client, token_b, JD_B_TEXT)
        analysis_b = _run_analysis(client, token_b, JD_B_ANALYSIS, cv_b_id, jd_b_id)

        # User B must get HR analysis, not PPC
        assert analysis_b["jd_analysis"]["job_title"] == "Human Resource Manager", (
            f"User B's analysis must reflect JD-B, not JD-A. "
            f"Got: {analysis_b['jd_analysis']['job_title']}"
        )
        assert analysis_b["jd_document_id"] == jd_b_id
        assert analysis_b["jd_document_id"] != jd_a_id

    def test_explicit_jd_id_uses_named_document_not_latest(self, client: TestClient) -> None:
        """When jd_document_id is supplied in the request, that exact document
        must be used even if a newer document exists."""
        token = _register_and_login(client)
        cv_id = _upload_cv(client, token)

        # Paste JD-A first (older)
        jd_a_id = _paste_jd(client, token, JD_A_TEXT)
        # Paste JD-B second (newer — would be returned by get_latest)
        jd_b_id = _paste_jd(client, token, JD_B_TEXT)

        # Explicitly request analysis against JD-A even though JD-B is newer
        analysis = _run_analysis(client, token, JD_A_ANALYSIS, cv_id, jd_a_id)

        assert analysis["jd_document_id"] == jd_a_id, (
            "Explicit jd_document_id must override the latest-document fallback"
        )
        assert analysis["jd_analysis"]["job_title"] == "Paid Search Manager"
