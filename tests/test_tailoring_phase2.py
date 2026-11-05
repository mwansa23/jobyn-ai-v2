"""Tests for Phase 2: JD analysis, CV-to-JD matching, and tailoring recommendations.

All AI calls are patched with deterministic mock responses — no real LLM
key is required. The test database is the shared SQLite instance from
conftest.py.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Constants ─────────────────────────────────────────────────────────────────

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
CV_UPLOAD_URL = "/api/v1/tailoring/cv/upload"
JD_PASTE_URL = "/api/v1/tailoring/job-description/paste"
ANALYZE_URL = "/api/v1/tailoring/analyze"
ANALYSIS_LATEST_URL = "/api/v1/tailoring/analysis/latest"
ANALYSIS_LIST_URL = "/api/v1/tailoring/analysis"

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# ── Patch targets ─────────────────────────────────────────────────────────────

_PATCH_JD_ANALYZE = "backend.ai.jd_analyzer.JDAnalyzerService.analyze"
_PATCH_CV_JD_MATCH = "backend.ai.cv_jd_matcher.CVJDMatcherService.match"
_PATCH_TAILORING_ADVISE = "backend.ai.tailoring_advisor.TailoringAdvisorService.advise"

# ── Mock AI responses ─────────────────────────────────────────────────────────

_MOCK_JD_ANALYSIS = {
    "job_title": "Senior Software Engineer",
    "employer": "TechCorp",
    "location": "Lusaka, Zambia",
    "employment_type": "full-time",
    "responsibilities": [
        "Design and build scalable backend services",
        "Lead technical architecture decisions",
        "Mentor junior engineers",
    ],
    "essential_qualifications": [
        "5+ years of Python experience",
        "Experience with FastAPI or Django",
        "PostgreSQL proficiency",
    ],
    "desirable_qualifications": [
        "AWS certification",
        "Kubernetes experience",
    ],
    "required_experience_years": "5 years",
    "technical_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes"],
    "soft_skills": ["Leadership", "Communication"],
    "industry_keywords": ["microservices", "CI/CD", "cloud-native"],
    "application_requirements": ["CV", "cover letter"],
    "summary": "A senior engineering role focused on platform and backend systems.",
}

_MOCK_MATCH_RESULT = {
    "matching_skills": [
        {"skill": "Python", "evidence": "5 years building Python services at TechCorp", "jd_reference": "5+ years Python"},
        {"skill": "FastAPI", "evidence": "Built REST APIs with FastAPI", "jd_reference": "FastAPI or Django"},
    ],
    "matching_experience": [
        {"requirement": "5+ years Python", "evidence": "Worked as Python developer for 6 years", "strength": "strong"},
        {"requirement": "Lead architecture decisions", "evidence": "Led the migration to microservices", "strength": "moderate"},
    ],
    "matching_achievements": [
        {"achievement": "Reduced API latency by 40%", "evidence": "Optimised query performance in billing service", "relevance": "Directly relevant to platform engineering"},
    ],
    "missing_requirements": [
        {"requirement": "Kubernetes experience", "type": "desirable", "note": "No mention of Kubernetes in CV"},
        {"requirement": "AWS certification", "type": "desirable", "note": "Not present in CV"},
    ],
    "missing_keywords": ["Kubernetes", "AWS"],
    "weak_sections": [
        {"section": "Summary", "issue": "Generic, not tailored to platform role", "evidence": "Passionate developer seeking new challenges"},
    ],
    "overall_match_summary": "Strong match for core Python/FastAPI requirements. Missing cloud and orchestration experience.",
}

_MOCK_RECOMMENDATIONS = {
    "section_recommendations": [
        {
            "section": "Summary",
            "action": "rewrite",
            "reason": "Generic summary does not target the platform role",
            "evidence": "Passionate developer seeking new challenges",
            "jd_reference": "Senior Software Engineer — Platform",
            "suggestion": "Rewrite to emphasise platform engineering and microservices experience.",
        },
        {
            "section": "Skills",
            "action": "reorganise",
            "reason": "Python and FastAPI should be prioritised",
            "evidence": "Skills listed alphabetically",
            "jd_reference": "Python, FastAPI",
            "suggestion": "Move Python and FastAPI to the top of the skills section.",
        },
    ],
    "achievements_to_highlight": [
        {
            "achievement": "Reduced API latency by 40%",
            "evidence": "Optimised query performance in billing service",
            "jd_reference": "Scalable backend services",
            "suggestion": "Add quantified impact and link to JD responsibilities.",
        },
    ],
    "information_to_request": [
        {
            "question": "Do you have any experience with Kubernetes or container orchestration?",
            "reason": "JD lists Kubernetes as desirable but it is not in your CV.",
            "section": "Skills",
        },
    ],
    "keyword_optimisation": [
        {"keyword": "microservices", "action": "integrate", "note": "Present in JD but not in CV skills section."},
        {"keyword": "CI/CD", "action": "add", "note": "Not mentioned at all in CV."},
    ],
    "priority_actions": [
        "Rewrite the summary to target the platform engineering role",
        "Reorganise skills section to prioritise Python and FastAPI",
        "Add CI/CD experience or note that it is a gap",
    ],
    "tailoring_summary": "Your CV has a strong Python/FastAPI foundation. Rewrite the summary, reorganise skills, and address cloud/orchestration gaps to maximise alignment with the platform role.",
}


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _unique_email() -> str:
    return f"phase2.{uuid.uuid4().hex[:8]}@example.com"


def _register_and_login(client: TestClient) -> str:
    email = _unique_email()
    pw = "TestPassword123!"
    r = client.post(REGISTER_URL, json={"email": email, "password": pw, "full_name": "Phase Two"})
    assert r.status_code == 201, r.text
    r = client.post(LOGIN_URL, json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_docx_bytes(text: str) -> bytes:
    from docx import Document
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


_REALISTIC_CV = """
Jane Smith
Senior Software Engineer

Experience:
- Senior Developer at TechCorp (2020-2024): Built Python REST APIs with FastAPI.
  Led the migration to microservices. Reduced API latency by 40%.
- Software Developer at DataStart (2018-2020): Python, PostgreSQL, Docker.

Skills: Python, FastAPI, PostgreSQL, Docker, Git, Linux
Education: BSc Computer Science, University of Zambia
"""

_REALISTIC_JD = """
Senior Software Engineer — Platform
TechCorp, Lusaka, Zambia (Hybrid)

About the role: We are looking for a Senior Software Engineer to join our Platform team.
Responsibilities: Design and build scalable backend services. Lead architecture decisions.
Requirements: 5+ years Python experience. FastAPI or Django. PostgreSQL. Docker.
Nice to have: Kubernetes, AWS certification.
Benefits: Salary K180,000-220,000, 25 days leave.
"""


def _setup_cv_and_jd(client: TestClient, token: str) -> None:
    """Upload a CV (DOCX) and paste a JD so analysis can run."""
    docx_bytes = _make_docx_bytes(_REALISTIC_CV)
    r = client.post(
        CV_UPLOAD_URL,
        headers=_auth_header(token),
        files={"file": ("cv.docx", docx_bytes, DOCX_CT)},
    )
    assert r.status_code == 201, r.text

    r = client.post(
        JD_PASTE_URL,
        headers=_auth_header(token),
        json={"text": _REALISTIC_JD},
    )
    assert r.status_code == 201, r.text


def _patch_all_ai():
    """Context manager that patches all three AI services."""
    return (
        patch(_PATCH_JD_ANALYZE, new=AsyncMock(return_value=_MOCK_JD_ANALYSIS)),
        patch(_PATCH_CV_JD_MATCH, new=AsyncMock(return_value=_MOCK_MATCH_RESULT)),
        patch(_PATCH_TAILORING_ADVISE, new=AsyncMock(return_value=_MOCK_RECOMMENDATIONS)),
    )


# ── Test Classes ──────────────────────────────────────────────────────────────


class TestRunAnalysis:
    """Tests for POST /tailoring/analyze."""

    def test_analysis_succeeds_with_cv_and_jd(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_cv_and_jd(client, token)

        jd_patch, match_patch, advise_patch = _patch_all_ai()
        with jd_patch, match_patch, advise_patch:
            r = client.post(ANALYZE_URL, headers=_auth_header(token), json={})
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["jd_analysis"] is not None
        assert data["jd_analysis"]["job_title"] == "Senior Software Engineer"
        assert data["match_result"] is not None
        assert len(data["match_result"]["matching_skills"]) == 2
        assert data["recommendations"] is not None
        assert len(data["recommendations"]["section_recommendations"]) == 2
        assert data["error"] is None

    def test_analysis_fails_without_cv(self, client: TestClient) -> None:
        token = _register_and_login(client)
        # Only paste a JD, no CV
        r = client.post(
            JD_PASTE_URL,
            headers=_auth_header(token),
            json={"text": _REALISTIC_JD},
        )
        assert r.status_code == 201

        r = client.post(ANALYZE_URL, headers=_auth_header(token), json={})
        assert r.status_code == 422
        assert "CV" in r.json()["detail"]

    def test_analysis_fails_without_jd(self, client: TestClient) -> None:
        token = _register_and_login(client)
        # Only upload a CV, no JD
        docx_bytes = _make_docx_bytes(_REALISTIC_CV)
        r = client.post(
            CV_UPLOAD_URL,
            headers=_auth_header(token),
            files={"file": ("cv.docx", docx_bytes, DOCX_CT)},
        )
        assert r.status_code == 201

        r = client.post(ANALYZE_URL, headers=_auth_header(token), json={})
        assert r.status_code == 422
        assert "job description" in r.json()["detail"].lower()

    def test_analysis_requires_auth(self, client: TestClient) -> None:
        r = client.post(ANALYZE_URL, json={})
        assert r.status_code in (401, 403)

    def test_jd_analysis_failure_persists_error(self, client: TestClient) -> None:
        from backend.ai.jd_analyzer import JDAnalysisError

        token = _register_and_login(client)
        _setup_cv_and_jd(client, token)

        with patch(_PATCH_JD_ANALYZE, new=AsyncMock(side_effect=JDAnalysisError("LLM down"))):
            r = client.post(ANALYZE_URL, headers=_auth_header(token), json={})
        assert r.status_code == 201  # analysis record is created with error
        data = r.json()
        assert data["error"] is not None
        assert "JD analysis failed" in data["error"]
        assert data["jd_analysis"] is None
        assert data["match_result"] is None

    def test_match_failure_persists_error_and_keeps_jd_analysis(self, client: TestClient) -> None:
        from backend.ai.cv_jd_matcher import CVJDMatcherError

        token = _register_and_login(client)
        _setup_cv_and_jd(client, token)

        with (
            patch(_PATCH_JD_ANALYZE, new=AsyncMock(return_value=_MOCK_JD_ANALYSIS)),
            patch(_PATCH_CV_JD_MATCH, new=AsyncMock(side_effect=CVJDMatcherError("LLM down"))),
        ):
            r = client.post(ANALYZE_URL, headers=_auth_header(token), json={})
        assert r.status_code == 201
        data = r.json()
        assert data["jd_analysis"] is not None  # JD analysis succeeded
        assert data["match_result"] is None  # match failed
        assert data["error"] is not None
        assert "matching" in data["error"].lower()


class TestGetAnalysis:
    """Tests for GET /tailoring/analysis/latest and /tailoring/analysis/{id}."""

    def test_get_latest_analysis(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_cv_and_jd(client, token)

        jd_patch, match_patch, advise_patch = _patch_all_ai()
        with jd_patch, match_patch, advise_patch:
            client.post(ANALYZE_URL, headers=_auth_header(token), json={})

        r = client.get(ANALYSIS_LATEST_URL, headers=_auth_header(token))
        assert r.status_code == 200
        data = r.json()
        assert data["jd_analysis"]["job_title"] == "Senior Software Engineer"
        assert data["recommendations"]["tailoring_summary"] is not None

    def test_get_latest_analysis_404_when_none(self, client: TestClient) -> None:
        token = _register_and_login(client)
        r = client.get(ANALYSIS_LATEST_URL, headers=_auth_header(token))
        assert r.status_code == 404

    def test_get_analysis_by_id(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_cv_and_jd(client, token)

        jd_patch, match_patch, advise_patch = _patch_all_ai()
        with jd_patch, match_patch, advise_patch:
            create_r = client.post(ANALYZE_URL, headers=_auth_header(token), json={})
        analysis_id = create_r.json()["id"]

        r = client.get(f"/api/v1/tailoring/analysis/{analysis_id}", headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()["id"] == analysis_id

    def test_cross_user_access_blocked(self, client: TestClient) -> None:
        token1 = _register_and_login(client)
        _setup_cv_and_jd(client, token1)

        jd_patch, match_patch, advise_patch = _patch_all_ai()
        with jd_patch, match_patch, advise_patch:
            create_r = client.post(ANALYZE_URL, headers=_auth_header(token1), json={})
        analysis_id = create_r.json()["id"]

        # Second user
        token2 = _register_and_login(client)
        r = client.get(f"/api/v1/tailoring/analysis/{analysis_id}", headers=_auth_header(token2))
        assert r.status_code == 404

    def test_list_analyses(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_cv_and_jd(client, token)

        jd_patch, match_patch, advise_patch = _patch_all_ai()
        with jd_patch, match_patch, advise_patch:
            client.post(ANALYZE_URL, headers=_auth_header(token), json={})
            client.post(ANALYZE_URL, headers=_auth_header(token), json={})

        r = client.get(ANALYSIS_LIST_URL, headers=_auth_header(token))
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_analysis_requires_auth(self, client: TestClient) -> None:
        r = client.get(ANALYSIS_LATEST_URL)
        assert r.status_code in (401, 403)


class TestJDAnalysisIntegrity:
    """Verify that JD analysis output has the expected structure."""

    def test_jd_analysis_has_all_fields(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_cv_and_jd(client, token)

        jd_patch, match_patch, advise_patch = _patch_all_ai()
        with jd_patch, match_patch, advise_patch:
            client.post(ANALYZE_URL, headers=_auth_header(token), json={})

        r = client.get(ANALYSIS_LATEST_URL, headers=_auth_header(token))
        data = r.json()
        jd = data["jd_analysis"]
        for field in [
            "job_title", "employer", "location", "employment_type",
            "responsibilities", "essential_qualifications", "desirable_qualifications",
            "required_experience_years", "technical_skills", "soft_skills",
            "industry_keywords", "application_requirements", "summary",
        ]:
            assert field in jd, f"Missing field: {field}"


class TestMatchResultIntegrity:
    """Verify that match results reference CV evidence."""

    def test_match_result_has_evidence(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_cv_and_jd(client, token)

        jd_patch, match_patch, advise_patch = _patch_all_ai()
        with jd_patch, match_patch, advise_patch:
            client.post(ANALYZE_URL, headers=_auth_header(token), json={})

        r = client.get(ANALYSIS_LATEST_URL, headers=_auth_header(token))
        data = r.json()
        match = data["match_result"]
        for ms in match["matching_skills"]:
            assert ms["evidence"], "matching_skill must have evidence"
            assert ms["jd_reference"], "matching_skill must have jd_reference"
        for me in match["matching_experience"]:
            assert me["evidence"], "matching_experience must have evidence"
            assert me["strength"] in ("strong", "moderate", "weak")
        for mr in match["missing_requirements"]:
            assert mr["requirement"], "missing_requirement must have requirement"
            assert mr["type"] in ("essential", "desirable")


class TestRecommendationsIntegrity:
    """Verify that recommendations are structured for Phase 3 consumption."""

    def test_recommendations_have_actions(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_cv_and_jd(client, token)

        jd_patch, match_patch, advise_patch = _patch_all_ai()
        with jd_patch, match_patch, advise_patch:
            client.post(ANALYZE_URL, headers=_auth_header(token), json={})

        r = client.get(ANALYSIS_LATEST_URL, headers=_auth_header(token))
        data = r.json()
        recs = data["recommendations"]
        for sr in recs["section_recommendations"]:
            assert sr["action"] in ("retain", "rewrite", "reorganise", "highlight", "add")
            assert sr["evidence"], "section_recommendation must have evidence"
            assert sr["section"], "section_recommendation must name the section"
        for info in recs["information_to_request"]:
            assert info["question"], "information_to_request must have a question"
            assert info["reason"], "information_to_request must have a reason"
        assert len(recs["priority_actions"]) > 0, "priority_actions must not be empty"
        assert recs["tailoring_summary"], "tailoring_summary must not be empty"


class TestPhase1Regression:
    """Verify Phase 1 functionality is not broken by Phase 2 additions."""

    def test_cv_upload_still_works(self, client: TestClient) -> None:
        token = _register_and_login(client)
        docx_bytes = _make_docx_bytes("Software Engineer with 5 years experience in Python.")
        r = client.post(
            CV_UPLOAD_URL,
            headers=_auth_header(token),
            files={"file": ("cv.docx", docx_bytes, DOCX_CT)},
        )
        assert r.status_code == 201
        assert r.json()["document_type"] == "cv"

    def test_jd_paste_still_works(self, client: TestClient) -> None:
        token = _register_and_login(client)
        r = client.post(
            JD_PASTE_URL,
            headers=_auth_header(token),
            json={"text": "Software Engineer role at TechCorp."},
        )
        assert r.status_code == 201
        assert r.json()["document_type"] == "job_description"
