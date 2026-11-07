"""Tests for Phase 3: CV generation, conversational editing, and document export.

All AI calls are patched with deterministic mock responses — no real LLM
key is required. The test database is the shared SQLite instance from
conftest.py.
"""

from __future__ import annotations

import io
import uuid
from copy import deepcopy
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
GENERATE_URL = "/api/v1/tailoring/cv/generate"
TAILORED_LATEST_URL = "/api/v1/tailoring/cv/tailored/latest"
EDIT_URL = "/api/v1/tailoring/cv/edit"
VERSIONS_URL = "/api/v1/tailoring/cv/versions"
REVERT_URL = "/api/v1/tailoring/cv/revert"

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# ── Patch targets ─────────────────────────────────────────────────────────────

_PATCH_REWRITER = "backend.ai.cv_rewriter.CVRewriterService.rewrite"
_PATCH_EDITOR = "backend.ai.cv_editor.CVEditorService.apply_edit"
_PATCH_JD_ANALYZE = "backend.ai.jd_analyzer.JDAnalyzerService.analyze"
_PATCH_CV_JD_MATCH = "backend.ai.cv_jd_matcher.CVJDMatcherService.match"
_PATCH_TAILORING_ADVISE = "backend.ai.tailoring_advisor.TailoringAdvisorService.advise"

# ── Mock AI responses ─────────────────────────────────────────────────────────

_MOCK_JD_ANALYSIS = {
    "job_title": "Senior Software Engineer",
    "employer": "TechCorp",
    "location": "Lusaka, Zambia",
    "employment_type": "full-time",
    "responsibilities": ["Design backend services", "Lead architecture"],
    "essential_qualifications": ["5+ years Python"],
    "desirable_qualifications": ["Kubernetes"],
    "required_experience_years": "5 years",
    "technical_skills": ["Python", "FastAPI", "PostgreSQL"],
    "soft_skills": ["Leadership"],
    "industry_keywords": ["microservices"],
    "application_requirements": ["CV"],
    "summary": "Senior platform engineering role.",
}

_MOCK_MATCH_RESULT = {
    "matching_skills": [{"skill": "Python", "evidence": "6 years Python", "jd_reference": "5+ years Python"}],
    "matching_experience": [{"requirement": "5+ years Python", "evidence": "6 years Python dev", "strength": "strong"}],
    "matching_achievements": [{"achievement": "Reduced latency 40%", "evidence": "Optimised billing queries", "relevance": "Platform eng"}],
    "missing_requirements": [{"requirement": "Kubernetes", "type": "desirable", "note": "Not in CV"}],
    "missing_keywords": ["Kubernetes"],
    "weak_sections": [{"section": "Summary", "issue": "Generic", "evidence": "Passionate developer"}],
    "overall_match_summary": "Strong Python match.",
}

_MOCK_RECOMMENDATIONS = {
    "section_recommendations": [
        {"section": "Summary", "action": "rewrite", "reason": "Generic", "evidence": "Passionate developer", "jd_reference": "Senior Platform Eng", "suggestion": "Target platform role"},
    ],
    "achievements_to_highlight": [{"achievement": "Reduced latency 40%", "evidence": "Optimised queries", "jd_reference": "Scalable services", "suggestion": "Quantify impact"}],
    "information_to_request": [{"question": "Any Kubernetes experience?", "reason": "JD wants it", "section": "Skills"}],
    "keyword_optimisation": [{"keyword": "microservices", "action": "integrate", "note": "In JD not CV"}],
    "priority_actions": ["Rewrite summary", "Reorganise skills"],
    "tailoring_summary": "Strong Python foundation. Address cloud gaps.",
}

_MOCK_REWRITTEN_CV = {
    "sections": [
        {"heading": "Contact", "type": "contact", "content": "Jane Smith\njane@example.com\n+260 97 123 4567", "items": [], "source": "original"},
        {"heading": "Professional Summary", "type": "summary", "content": "Senior Software Engineer with 6 years of experience building scalable Python backend services. Proven track record of optimising API performance and leading microservices migrations.", "items": [], "source": "rewritten"},
        {"heading": "Experience", "type": "experience", "content": "", "items": [
            {"title": "Senior Developer", "company": "TechCorp (2020-2024)", "details": ["Built Python REST APIs with FastAPI", "Led migration to microservices architecture", "Reduced API latency by 40% through query optimisation"]},
            {"title": "Software Developer", "company": "DataStart (2018-2020)", "details": ["Developed Python data pipelines with PostgreSQL", "Containerised applications with Docker"]},
        ], "source": "rewritten"},
        {"heading": "Skills", "type": "skills", "content": "", "items": ["Python", "FastAPI", "PostgreSQL", "Docker", "Git", "Linux"], "source": "rewritten"},
        {"heading": "Education", "type": "education", "content": "", "items": ["BSc Computer Science, University of Zambia"], "source": "original"},
    ],
    "generation_notes": "Rewrote summary for platform role. Reorganised skills. Preserved education and contact verbatim.",
}

_MOCK_EDITED_CV = {
    "sections": [
        {"heading": "Contact", "type": "contact", "content": "Jane Smith\njane@example.com\n+260 97 123 4567", "items": [], "source": "original"},
        {"heading": "Professional Summary", "type": "summary", "content": "Senior Software Engineer with 6 years of experience building scalable Python backend services. Specialised in microservices architecture and API performance optimisation. Proven track record of reducing latency by 40%.", "items": [], "source": "rewritten"},
        {"heading": "Experience", "type": "experience", "content": "", "items": [
            {"title": "Senior Developer", "company": "TechCorp (2020-2024)", "details": ["Built Python REST APIs with FastAPI", "Led migration to microservices architecture", "Reduced API latency by 40% through query optimisation"]},
            {"title": "Software Developer", "company": "DataStart (2018-2020)", "details": ["Developed Python data pipelines with PostgreSQL", "Containerised applications with Docker"]},
        ], "source": "rewritten"},
        {"heading": "Skills", "type": "skills", "content": "", "items": ["Python", "FastAPI", "PostgreSQL", "Docker", "Git", "Linux", "Microservices"], "source": "rewritten"},
        {"heading": "Education", "type": "education", "content": "", "items": ["BSc Computer Science, University of Zambia"], "source": "original"},
    ],
    "edit_summary": "Improved professional summary to emphasise microservices specialisation. Added Microservices to skills.",
}


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _unique_email() -> str:
    return f"phase3.{uuid.uuid4().hex[:8]}@example.com"


def _register_and_login(client: TestClient) -> str:
    email = _unique_email()
    pw = "TestPassword123!"
    r = client.post(REGISTER_URL, json={"email": email, "password": pw, "full_name": "Phase Three"})
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
"""


def _setup_full_pipeline(client: TestClient, token: str) -> None:
    """Upload CV, paste JD, run analysis — all with mocked AI."""
    docx_bytes = _make_docx_bytes(_REALISTIC_CV)
    r = client.post(CV_UPLOAD_URL, headers=_auth_header(token),
                    files={"file": ("cv.docx", docx_bytes, DOCX_CT)})
    assert r.status_code == 201, r.text

    r = client.post(JD_PASTE_URL, headers=_auth_header(token),
                    json={"text": _REALISTIC_JD})
    assert r.status_code == 201, r.text

    with (
        patch(_PATCH_JD_ANALYZE, new=AsyncMock(return_value=_MOCK_JD_ANALYSIS)),
        patch(_PATCH_CV_JD_MATCH, new=AsyncMock(return_value=_MOCK_MATCH_RESULT)),
        patch(_PATCH_TAILORING_ADVISE, new=AsyncMock(return_value=_MOCK_RECOMMENDATIONS)),
    ):
        r = client.post(ANALYZE_URL, headers=_auth_header(token), json={})
        assert r.status_code == 201, r.text


# ── Test Classes ──────────────────────────────────────────────────────────────


class TestGenerateTailoredCV:
    """Tests for POST /tailoring/cv/generate."""

    def test_generate_succeeds_after_analysis(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)

        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            r = client.post(GENERATE_URL, headers=_auth_header(token), json={})
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["version_number"] == 1
        assert data["edit_type"] == "initial"
        assert data["status"] == "pending"
        assert len(data["content"]["sections"]) == 5
        assert data["content"]["sections"][1]["type"] == "summary"
        assert "generation_notes" in data["content"]

    def test_generate_fails_without_analysis(self, client: TestClient) -> None:
        token = _register_and_login(client)
        # Upload CV but no analysis
        docx_bytes = _make_docx_bytes(_REALISTIC_CV)
        client.post(CV_UPLOAD_URL, headers=_auth_header(token),
                    files={"file": ("cv.docx", docx_bytes, DOCX_CT)})

        r = client.post(GENERATE_URL, headers=_auth_header(token), json={})
        assert r.status_code == 422
        assert "analysis" in r.json()["detail"].lower()

    def test_generate_requires_auth(self, client: TestClient) -> None:
        r = client.post(GENERATE_URL, json={})
        assert r.status_code in (401, 403)


class TestGetTailoredCV:
    """Tests for GET /cv/tailored/latest and GET /cv/{id}."""

    def test_get_latest(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)

        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            client.post(GENERATE_URL, headers=_auth_header(token), json={})

        r = client.get(TAILORED_LATEST_URL, headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()["version_number"] == 1

    def test_get_latest_404_when_none(self, client: TestClient) -> None:
        token = _register_and_login(client)
        r = client.get(TAILORED_LATEST_URL, headers=_auth_header(token))
        assert r.status_code == 404

    def test_preview_historical_version_is_read_only_and_compared(
        self, client: TestClient
    ) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            initial = client.post(
                GENERATE_URL, headers=_auth_header(token), json={}
            ).json()
        with patch(_PATCH_EDITOR, new=AsyncMock(return_value=_MOCK_EDITED_CV)):
            client.post(
                EDIT_URL,
                headers=_auth_header(token),
                json={"message": "Emphasise microservices"},
            )

        preview = client.get(
            f"/api/v1/tailoring/cv/{initial['id']}", headers=_auth_header(token)
        )
        assert preview.status_code == 200
        data = preview.json()
        assert data["version_number"] == 1
        assert data["content"]["sections"] == _MOCK_REWRITTEN_CV["sections"]
        comparison = {
            entry["heading"]: entry for entry in data["content"]["comparison"]
        }
        assert comparison["Professional Summary"]["status"] == "changed"
        assert comparison["Professional Summary"]["before"] == (
            _MOCK_EDITED_CV["sections"][1]
        )
        assert comparison["Professional Summary"]["after"] == (
            _MOCK_REWRITTEN_CV["sections"][1]
        )
        assert comparison["Education"]["status"] == "unchanged"

        latest = client.get(TAILORED_LATEST_URL, headers=_auth_header(token)).json()
        versions = client.get(VERSIONS_URL, headers=_auth_header(token)).json()
        assert latest["version_number"] == 2
        assert versions["total"] == 2

    def test_preview_requires_authentication(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            cv = client.post(
                GENERATE_URL, headers=_auth_header(token), json={}
            ).json()
        response = client.get(f"/api/v1/tailoring/cv/{cv['id']}")
        assert response.status_code in (401, 403)

    def test_cross_user_access_blocked(self, client: TestClient) -> None:
        token1 = _register_and_login(client)
        _setup_full_pipeline(client, token1)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            r = client.post(GENERATE_URL, headers=_auth_header(token1), json={})
        cv_id = r.json()["id"]

        token2 = _register_and_login(client)
        r = client.get(f"/api/v1/tailoring/cv/{cv_id}", headers=_auth_header(token2))
        assert r.status_code == 404


class TestConversationalEdit:
    """Tests for POST /tailoring/cv/edit."""

    def test_section_edit_preserves_unrelated_sections(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            client.post(GENERATE_URL, headers=_auth_header(token), json={})

        model_response = deepcopy(_MOCK_EDITED_CV)
        model_response["sections"][3]["items"].append("Unrequested skill")
        model_response["sections"][4]["items"] = ["Changed education"]
        model_response["section_explanations"] = [
            {
                "section_index": 1,
                "explanation": "The request asked to target the platform engineering role.",
            }
        ]
        editor = AsyncMock(return_value=model_response)
        with patch("backend.ai.cv_editor.generate_json", new=editor):
            response = client.post(
                EDIT_URL,
                headers=_auth_header(token),
                json={"message": "Rewrite my summary for a platform engineering role"},
            )

        assert response.status_code == 201, response.text
        sections = response.json()["content"]["sections"]
        assert sections[1]["content"] == model_response["sections"][1]["content"]
        assert sections[3] == _MOCK_REWRITTEN_CV["sections"][3]
        assert sections[4] == _MOCK_REWRITTEN_CV["sections"][4]
        comparison = response.json()["content"]["comparison"]
        assert comparison[1]["status"] == "changed"
        assert comparison[1]["before"] == _MOCK_REWRITTEN_CV["sections"][1]
        assert comparison[1]["after"] == sections[1]
        assert comparison[1]["explanation"] == (
            "The request asked to target the platform engineering role."
        )
        assert all(
            comparison[index]["status"] == "unchanged"
            for index in (0, 2, 3, 4)
        )
        prompt = editor.await_args.args[0]
        assert "Only section index 1" in prompt

    def test_edit_clarification_does_not_create_version(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            client.post(GENERATE_URL, headers=_auth_header(token), json={})

        clarification = {
            "sections": [],
            "edit_summary": "",
            "needs_clarification": True,
            "clarification_question": "How many years of Kubernetes experience do you have?",
        }
        with patch(
            "backend.ai.cv_editor.generate_json",
            new=AsyncMock(return_value=clarification),
        ):
            response = client.post(
                EDIT_URL,
                headers=_auth_header(token),
                json={"message": "Add my Kubernetes experience to the skills section"},
            )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "needs_clarification": True,
            "clarification_question": clarification["clarification_question"],
        }
        versions = client.get(VERSIONS_URL, headers=_auth_header(token)).json()
        assert versions["total"] == 1
        assert versions["items"][0]["version_number"] == 1

    def test_unscoped_request_keeps_whole_cv_edit_compatibility(
        self, client: TestClient
    ) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            client.post(GENERATE_URL, headers=_auth_header(token), json={})

        model_response = deepcopy(_MOCK_EDITED_CV)
        model_response["sections"] = [
            model_response["sections"][0],
            model_response["sections"][2],
            model_response["sections"][1],
            model_response["sections"][3],
            model_response["sections"][4],
        ]
        model_response["section_explanations"] = [
            {
                "section_index": 2,
                "explanation": "This follows the request to emphasize the existing experience.",
            },
            {
                "section_index": 3,
                "explanation": "Microservices is supported by the existing experience entry.",
            },
        ]
        editor = AsyncMock(return_value=model_response)
        with patch("backend.ai.cv_editor.generate_json", new=editor):
            response = client.post(
                EDIT_URL,
                headers=_auth_header(token),
                json={"message": "Emphasise my microservices experience"},
            )

        assert response.status_code == 201, response.text
        sections = response.json()["content"]["sections"]
        assert sections[2]["content"] == _MOCK_EDITED_CV["sections"][1]["content"]
        assert sections[3]["items"] == _MOCK_EDITED_CV["sections"][3]["items"]
        comparison = response.json()["content"]["comparison"]
        assert [entry["status"] for entry in comparison] == [
            "unchanged",
            "changed",
            "unchanged",
            "changed",
            "unchanged",
        ]
        comparison_by_heading = {entry["heading"]: entry for entry in comparison}
        assert comparison_by_heading["Professional Summary"]["before"] == (
            _MOCK_REWRITTEN_CV["sections"][1]
        )
        assert comparison_by_heading["Professional Summary"]["after"] == sections[2]
        assert comparison_by_heading["Professional Summary"]["explanation"] == (
            "This follows the request to emphasize the existing experience."
        )
        assert comparison_by_heading["Experience"]["before"] == (
            _MOCK_REWRITTEN_CV["sections"][2]
        )
        assert comparison_by_heading["Experience"]["after"] == sections[1]
        assert comparison_by_heading["Experience"]["status"] == "unchanged"
        assert comparison_by_heading["Experience"]["position_changed"] is True
        assert "explanation" not in comparison_by_heading["Experience"]

        assert comparison_by_heading["Skills"]["explanation"] == (
            "Microservices is supported by the existing experience entry."
        )
        assert "Only section index" not in editor.await_args.args[0]

    def test_edit_creates_new_version(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)

        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            client.post(GENERATE_URL, headers=_auth_header(token), json={})

        with patch(_PATCH_EDITOR, new=AsyncMock(return_value=_MOCK_EDITED_CV)):
            r = client.post(EDIT_URL, headers=_auth_header(token),
                            json={"message": "Emphasise my microservices experience"})
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["version_number"] == 2
        assert data["edit_type"] == "edit"
        assert data["edit_request"] == "Emphasise my microservices experience"
        assert "Microservices" in data["content"]["sections"][3]["items"]

    def test_edit_fails_without_existing_cv(self, client: TestClient) -> None:
        token = _register_and_login(client)
        r = client.post(EDIT_URL, headers=_auth_header(token),
                        json={"message": "Improve summary"})
        assert r.status_code == 422

    def test_edit_preserves_previous_version(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)

        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            client.post(GENERATE_URL, headers=_auth_header(token), json={})

        with patch(_PATCH_EDITOR, new=AsyncMock(return_value=_MOCK_EDITED_CV)):
            client.post(EDIT_URL, headers=_auth_header(token),
                        json={"message": "Emphasise microservices"})

        # Original version 1 should still exist
        r = client.get(VERSIONS_URL, headers=_auth_header(token))
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        versions = [v["version_number"] for v in data["items"]]
        assert 1 in versions and 2 in versions

    def test_edit_requires_auth(self, client: TestClient) -> None:
        r = client.post(EDIT_URL, json={"message": "test"})
        assert r.status_code in (401, 403)


class TestAcceptReject:
    """Tests for PATCH /cv/{id}/status."""

    def test_accept_cv(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            r = client.post(GENERATE_URL, headers=_auth_header(token), json={})
        cv_id = r.json()["id"]

        r = client.patch(f"/api/v1/tailoring/cv/{cv_id}/status",
                         headers=_auth_header(token), json={"status": "accepted"})
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"

    def test_reject_cv(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            r = client.post(GENERATE_URL, headers=_auth_header(token), json={})
        cv_id = r.json()["id"]

        r = client.patch(f"/api/v1/tailoring/cv/{cv_id}/status",
                         headers=_auth_header(token), json={"status": "rejected"})
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    def test_invalid_status_rejected(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            r = client.post(GENERATE_URL, headers=_auth_header(token), json={})
        cv_id = r.json()["id"]

        r = client.patch(f"/api/v1/tailoring/cv/{cv_id}/status",
                         headers=_auth_header(token), json={"status": "invalid"})
        assert r.status_code == 422


class TestRevert:
    """Tests for POST /cv/revert."""

    def test_revert_to_earlier_version(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            client.post(GENERATE_URL, headers=_auth_header(token), json={})
        with patch(_PATCH_EDITOR, new=AsyncMock(return_value=_MOCK_EDITED_CV)):
            client.post(EDIT_URL, headers=_auth_header(token),
                        json={"message": "Add microservices"})

        editor = AsyncMock()
        with patch(_PATCH_EDITOR, new=editor):
            r = client.post(REVERT_URL, headers=_auth_header(token),
                            json={"version_number": 1})
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["version_number"] == 3
        assert data["edit_type"] == "revert"
        assert data["change_summary"] == "Reverted to version 1."
        assert data["content"]["sections"] == _MOCK_REWRITTEN_CV["sections"]
        assert data["content"]["generation_notes"] == _MOCK_REWRITTEN_CV["generation_notes"]
        editor.assert_not_awaited()
        comparison = data["content"]["comparison"]
        assert comparison[1]["status"] == "changed"
        assert comparison[1]["before"] == _MOCK_EDITED_CV["sections"][1]
        assert comparison[1]["after"] == _MOCK_REWRITTEN_CV["sections"][1]
        assert comparison[1]["explanation"] == "Restored from version 1."
        versions = client.get(VERSIONS_URL, headers=_auth_header(token)).json()
        assert versions["total"] == 3
        initial = next(item for item in versions["items"] if item["version_number"] == 1)
        assert initial["content"] == _MOCK_REWRITTEN_CV

    def test_revert_nonexistent_version(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            client.post(GENERATE_URL, headers=_auth_header(token), json={})

        r = client.post(REVERT_URL, headers=_auth_header(token),
                        json={"version_number": 99})
        assert r.status_code == 404


class TestExport:
    """Tests for DOCX and PDF export."""

    def test_export_docx_returns_valid_file(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            r = client.post(GENERATE_URL, headers=_auth_header(token), json={})
        cv_id = r.json()["id"]

        r = client.get(f"/api/v1/tailoring/cv/{cv_id}/export/docx",
                       headers=_auth_header(token))
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert len(r.content) > 100  # valid DOCX should be non-trivial
        # Verify it's a valid ZIP (DOCX is a ZIP file)
        import zipfile
        assert zipfile.is_zipfile(io.BytesIO(r.content))

    def test_export_pdf_returns_valid_file(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            r = client.post(GENERATE_URL, headers=_auth_header(token), json={})
        cv_id = r.json()["id"]

        r = client.get(f"/api/v1/tailoring/cv/{cv_id}/export/pdf",
                       headers=_auth_header(token))
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert len(r.content) > 100
        # Verify it's a valid PDF (starts with %PDF)
        assert r.content[:5] == b"%PDF-"

    def test_export_cross_user_blocked(self, client: TestClient) -> None:
        token1 = _register_and_login(client)
        _setup_full_pipeline(client, token1)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            r = client.post(GENERATE_URL, headers=_auth_header(token1), json={})
        cv_id = r.json()["id"]

        token2 = _register_and_login(client)
        r = client.get(f"/api/v1/tailoring/cv/{cv_id}/export/docx",
                       headers=_auth_header(token2))
        assert r.status_code == 404

    def test_export_requires_auth(self, client: TestClient) -> None:
        r = client.get(f"/api/v1/tailoring/cv/{uuid.uuid4()}/export/docx")
        assert r.status_code in (401, 403)


class TestExportContentVerification:
    """Verify exported files contain the approved content."""

    def test_docx_contains_cv_sections(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            r = client.post(GENERATE_URL, headers=_auth_header(token), json={})
        cv_id = r.json()["id"]

        r = client.get(f"/api/v1/tailoring/cv/{cv_id}/export/docx",
                       headers=_auth_header(token))
        assert r.status_code == 200

        # Parse the DOCX and check content
        from docx import Document
        doc = Document(io.BytesIO(r.content))
        text = "\n".join(p.text for p in doc.paragraphs)
        assert "Professional Summary" in text or "Summary" in text
        assert "Python" in text
        assert "TechCorp" in text

    def test_pdf_contains_cv_sections(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            r = client.post(GENERATE_URL, headers=_auth_header(token), json={})
        cv_id = r.json()["id"]

        r = client.get(f"/api/v1/tailoring/cv/{cv_id}/export/pdf",
                       headers=_auth_header(token))
        assert r.status_code == 200

        # Parse the PDF and check content
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(r.content))
        text = "\n".join(page.extract_text() for page in reader.pages)
        assert "Summary" in text or "Professional" in text
        assert "Python" in text
        assert "TechCorp" in text


class TestIntegrityPreservation:
    """Verify that the rewriting preserves original information."""

    def test_contact_section_preserved_verbatim(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            client.post(GENERATE_URL, headers=_auth_header(token), json={})

        r = client.get(TAILORED_LATEST_URL, headers=_auth_header(token))
        data = r.json()
        contact = [s for s in data["content"]["sections"] if s["type"] == "contact"][0]
        assert contact["source"] == "original"
        assert "Jane Smith" in contact["content"]

    def test_education_section_preserved(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            client.post(GENERATE_URL, headers=_auth_header(token), json={})

        r = client.get(TAILORED_LATEST_URL, headers=_auth_header(token))
        data = r.json()
        education = [s for s in data["content"]["sections"] if s["type"] == "education"][0]
        assert education["source"] == "original"
        assert "University of Zambia" in education["items"][0]

    def test_rewritten_sections_marked(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        with patch(_PATCH_REWRITER, new=AsyncMock(return_value=_MOCK_REWRITTEN_CV)):
            client.post(GENERATE_URL, headers=_auth_header(token), json={})

        r = client.get(TAILORED_LATEST_URL, headers=_auth_header(token))
        data = r.json()
        summary = [s for s in data["content"]["sections"] if s["type"] == "summary"][0]
        assert summary["source"] == "rewritten"


class TestRegression:
    """Phase 1 and Phase 2 regression."""

    def test_phase1_cv_upload_still_works(self, client: TestClient) -> None:
        token = _register_and_login(client)
        docx_bytes = _make_docx_bytes("Software Engineer with 5 years Python.")
        r = client.post(CV_UPLOAD_URL, headers=_auth_header(token),
                        files={"file": ("cv.docx", docx_bytes, DOCX_CT)})
        assert r.status_code == 201

    def test_phase1_jd_paste_still_works(self, client: TestClient) -> None:
        token = _register_and_login(client)
        r = client.post(JD_PASTE_URL, headers=_auth_header(token),
                        json={"text": "Software Engineer role at TechCorp."})
        assert r.status_code == 201

    def test_phase2_analysis_still_works(self, client: TestClient) -> None:
        token = _register_and_login(client)
        _setup_full_pipeline(client, token)
        r = client.get(ANALYSIS_LATEST_URL, headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()["jd_analysis"]["job_title"] == "Senior Software Engineer"
