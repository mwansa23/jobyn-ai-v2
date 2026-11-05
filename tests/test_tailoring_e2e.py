"""Phase 3 End-to-End Verification Test.

Exercises the complete candidate workflow through the API using the
shared test client and database from conftest.py.
All AI calls are patched with deterministic mocks.
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
GENERATE_URL = "/api/v1/tailoring/cv/generate"
EDIT_URL = "/api/v1/tailoring/cv/edit"
VERSIONS_URL = "/api/v1/tailoring/cv/versions"
REVERT_URL = "/api/v1/tailoring/cv/revert"
TAILORED_LATEST = "/api/v1/tailoring/cv/tailored/latest"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# ── Patch targets ─────────────────────────────────────────────────────────────

PATCH_JD = "backend.ai.jd_analyzer.JDAnalyzerService.analyze"
PATCH_MATCH = "backend.ai.cv_jd_matcher.CVJDMatcherService.match"
PATCH_RECS = "backend.ai.tailoring_advisor.TailoringAdvisorService.advise"
PATCH_REWRITE = "backend.ai.cv_rewriter.CVRewriterService.rewrite"
PATCH_EDIT = "backend.ai.cv_editor.CVEditorService.apply_edit"

# ── Mock data ──────────────────────────────────────────────────────────────────

MOCK_JD_ANALYSIS = {
    "job_title": "Senior Backend Engineer",
    "employer": "CloudScale Ltd",
    "location": "Lusaka, Zambia",
    "employment_type": "full-time",
    "responsibilities": ["Design scalable APIs", "Lead backend architecture"],
    "essential_qualifications": ["5+ years Python", "FastAPI or Django", "PostgreSQL"],
    "desirable_qualifications": ["Kubernetes", "AWS certification"],
    "required_experience_years": "5 years",
    "technical_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes"],
    "soft_skills": ["Leadership"],
    "industry_keywords": ["microservices", "API design"],
    "application_requirements": ["CV"],
    "summary": "Senior backend engineering role.",
}

MOCK_MATCH = {
    "matching_skills": [{"skill": "Python", "evidence": "6 years Python", "jd_reference": "5+ years Python"}],
    "matching_experience": [{"requirement": "5+ years Python", "evidence": "6 years Python dev", "strength": "strong"}],
    "matching_achievements": [{"achievement": "Reduced latency 40%", "evidence": "Optimised queries", "relevance": "Scalable APIs"}],
    "missing_requirements": [{"requirement": "Kubernetes", "type": "desirable", "note": "Not in CV"}],
    "missing_keywords": ["Kubernetes"],
    "weak_sections": [{"section": "Summary", "issue": "Generic", "evidence": "Passionate developer"}],
    "overall_match_summary": "Strong Python match.",
}

MOCK_RECS = {
    "section_recommendations": [
        {"section": "Summary", "action": "rewrite", "reason": "Generic", "evidence": "Passionate developer", "jd_reference": "Senior Backend Eng", "suggestion": "Target backend role"},
    ],
    "achievements_to_highlight": [{"achievement": "Reduced latency 40%", "evidence": "Optimised queries", "jd_reference": "Scalable APIs", "suggestion": "Quantify"}],
    "information_to_request": [{"question": "Kubernetes experience?", "reason": "JD wants it", "section": "Skills"}],
    "keyword_optimisation": [{"keyword": "microservices", "action": "integrate", "note": "In JD not CV"}],
    "priority_actions": ["Rewrite summary", "Reorganise skills"],
    "tailoring_summary": "Strong Python. Rewrite summary and address cloud gaps.",
}

MOCK_CV_V1 = {
    "sections": [
        {"heading": "Contact", "type": "contact", "content": "Jane Smith\njane@example.com\n+260 97 1234567", "items": [], "source": "original"},
        {"heading": "Professional Summary", "type": "summary", "content": "Senior Software Engineer with 6 years building scalable Python APIs.", "items": [], "source": "rewritten"},
        {"heading": "Experience", "type": "experience", "content": "", "items": [
            {"title": "Senior Developer", "company": "TechCorp (2020-2024)", "details": ["Built Python REST APIs with FastAPI", "Led microservices migration", "Reduced API latency by 40%"]},
            {"title": "Developer", "company": "DataStart (2018-2020)", "details": ["Built Python data pipelines", "Used PostgreSQL and Docker"]},
        ], "source": "rewritten"},
        {"heading": "Skills", "type": "skills", "content": "", "items": ["Python", "FastAPI", "PostgreSQL", "Docker", "Git", "Linux"], "source": "rewritten"},
        {"heading": "Education", "type": "education", "content": "", "items": ["BSc Computer Science, University of Zambia (2014-2018)"], "source": "original"},
    ],
    "generation_notes": "Rewrote summary. Reorganised skills. Preserved education and contact verbatim.",
}

MOCK_CV_V2 = {
    "sections": [
        {"heading": "Contact", "type": "contact", "content": "Jane Smith\njane@example.com\n+260 97 1234567", "items": [], "source": "original"},
        {"heading": "Professional Summary", "type": "summary", "content": "Senior Backend Engineer with 6 years building high-performance Python APIs. Specialised in microservices. Reduced API latency by 40%.", "items": [], "source": "rewritten"},
        {"heading": "Experience", "type": "experience", "content": "", "items": [
            {"title": "Senior Developer", "company": "TechCorp (2020-2024)", "details": ["Built Python REST APIs with FastAPI", "Led microservices migration", "Reduced API latency by 40%"]},
            {"title": "Developer", "company": "DataStart (2018-2020)", "details": ["Built Python data pipelines", "Used PostgreSQL and Docker"]},
        ], "source": "rewritten"},
        {"heading": "Skills", "type": "skills", "content": "", "items": ["Python", "FastAPI", "PostgreSQL", "Docker", "Git", "Linux", "Microservices"], "source": "rewritten"},
        {"heading": "Education", "type": "education", "content": "", "items": ["BSc Computer Science, University of Zambia (2014-2018)"], "source": "original"},
    ],
    "edit_summary": "Rewrote summary for backend role. Added microservices keyword.",
}

MOCK_CV_V3 = {
    "sections": [
        {"heading": "Contact", "type": "contact", "content": "Jane Smith\njane@example.com\n+260 97 1234567", "items": [], "source": "original"},
        {"heading": "Professional Summary", "type": "summary", "content": "Senior Backend Engineer with 6 years building high-performance Python APIs. Specialised in microservices. Reduced API latency by 40%.", "items": [], "source": "rewritten"},
        {"heading": "Experience", "type": "experience", "content": "", "items": [
            {"title": "Senior Developer", "company": "TechCorp (2020-2024)", "details": ["Built scalable Python REST APIs with FastAPI serving 1M+ daily requests", "Led microservices migration reducing deployment time by 60%", "Reduced API latency by 40% through query optimisation and caching"]},
            {"title": "Developer", "company": "DataStart (2018-2020)", "details": ["Built Python data pipelines", "Used PostgreSQL and Docker"]},
        ], "source": "rewritten"},
        {"heading": "Skills", "type": "skills", "content": "", "items": ["Python", "FastAPI", "PostgreSQL", "Docker", "Git", "Linux", "Microservices"], "source": "rewritten"},
        {"heading": "Education", "type": "education", "content": "", "items": ["BSc Computer Science, University of Zambia (2014-2018)"], "source": "original"},
    ],
    "edit_summary": "Improved achievement statements with quantified impact.",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _email() -> str:
    return f"e2e_{uuid.uuid4().hex[:8]}@example.com"

def _register_and_login(client):
    email = _email()
    pw = "E2ePassword123!"
    r = client.post(REGISTER_URL, json={"email": email, "password": pw, "full_name": "E2E Test"})
    assert r.status_code == 201, r.text
    r = client.post(LOGIN_URL, json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]

def _auth(token):
    return {"Authorization": f"Bearer {token}"}

def _make_docx(text):
    from docx import Document
    doc = Document()
    for line in text.strip().split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

REALISTIC_CV = """
Jane Smith
Senior Software Engineer
jane@example.com | +260 97 1234567
Summary:
Senior software engineer with 6 years experience building Python web applications.
Experience:
- Senior Developer at TechCorp (2020-2024): Built Python REST APIs with FastAPI. Led microservices migration. Reduced API latency by 40%.
- Developer at DataStart (2018-2020): Built Python data pipelines. Used PostgreSQL and Docker.
Skills: Python, FastAPI, PostgreSQL, Docker, Git, Linux
Education: BSc Computer Science, University of Zambia (2014-2018)
"""

REALISTIC_JD = """
Senior Backend Engineer - Platform
CloudScale Ltd, Lusaka, Zambia (Hybrid)
About the role: We are looking for a Senior Backend Engineer to join our Platform team.
Responsibilities: Design scalable APIs, lead backend architecture, mentor developers.
Requirements: 5+ years Python experience, FastAPI or Django, PostgreSQL, Docker.
Nice to have: Kubernetes, AWS certification.
Benefits: Salary K180,000-220,000, 25 days leave.
"""


# ── Test Class ─────────────────────────────────────────────────────────────────

class TestEndToEndWorkflow:
    """Complete candidate workflow from CV upload to document download."""

    def test_full_workflow(self, client: TestClient):
        """Exercise every step of the candidate journey."""
        token = _register_and_login(client)
        auth = _auth(token)

        # 1. Upload CV
        cv_bytes = _make_docx(REALISTIC_CV)
        r = client.post(CV_UPLOAD_URL, headers=auth,
                        files={"file": ("cv.docx", cv_bytes, DOCX_CT)})
        assert r.status_code == 201
        assert r.json()["extraction_status"] == "succeeded"

        # 2. Paste JD
        r = client.post(JD_PASTE_URL, headers=auth, json={"text": REALISTIC_JD})
        assert r.status_code == 201

        # 3. Run analysis
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            r = client.post(ANALYZE_URL, headers=auth, json={})
        assert r.status_code == 201
        assert r.json()["jd_analysis"] is not None
        assert r.json()["recommendations"] is not None

        # 4. Generate tailored CV
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_V1)):
            r = client.post(GENERATE_URL, headers=auth, json={})
        assert r.status_code == 201
        v1 = r.json()
        assert v1["version_number"] == 1
        assert len(v1["content"]["sections"]) == 5
        assert v1["content"]["sections"][0]["source"] == "original"  # contact
        assert v1["content"]["sections"][4]["source"] == "original"  # education

        # 5. Edit 1: rewrite summary
        with patch(PATCH_EDIT, new=AsyncMock(return_value=MOCK_CV_V2)):
            r = client.post(EDIT_URL, headers=auth, json={"message": "Rewrite my professional summary"})
        assert r.status_code == 201
        v2 = r.json()
        assert v2["version_number"] == 2
        v2_summary = [s for s in v2["content"]["sections"] if s["type"] == "summary"][0]
        v1_summary = [s for s in v1["content"]["sections"] if s["type"] == "summary"][0]
        assert v2_summary["content"] != v1_summary["content"]
        assert "Backend Engineer" in v2_summary["content"]

        # 6. Edit 2: improve achievements
        with patch(PATCH_EDIT, new=AsyncMock(return_value=MOCK_CV_V3)):
            r = client.post(EDIT_URL, headers=auth, json={"message": "Improve my achievement statements at TechCorp"})
        assert r.status_code == 201
        v3 = r.json()
        assert v3["version_number"] == 3

        # 7. Version history
        r = client.get(VERSIONS_URL, headers=auth)
        assert r.status_code == 200
        assert r.json()["total"] == 3

        # 8. Revert to version 1
        r = client.post(REVERT_URL, headers=auth, json={"version_number": 1})
        assert r.status_code == 201
        v4 = r.json()
        assert v4["version_number"] == 4
        assert v4["edit_type"] == "revert"
        v4_summary = [s for s in v4["content"]["sections"] if s["type"] == "summary"][0]
        assert v4_summary["content"] == v1_summary["content"]

        # 9. Accept version 4
        r = client.patch(f"/api/v1/tailoring/cv/{v4['id']}/status", headers=auth, json={"status": "accepted"})
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"

        # Verify status persists
        r = client.get(f"/api/v1/tailoring/cv/{v4['id']}", headers=auth)
        assert r.json()["status"] == "accepted"

        # 10. Download DOCX
        r = client.get(f"/api/v1/tailoring/cv/{v4['id']}/export/docx", headers=auth)
        assert r.status_code == 200
        assert len(r.content) > 100
        import zipfile
        assert zipfile.is_zipfile(io.BytesIO(r.content))

        # Verify DOCX content
        from docx import Document
        doc = Document(io.BytesIO(r.content))
        text = "\n".join(p.text for p in doc.paragraphs)
        assert "Jane Smith" in text
        assert "Python" in text
        assert "TechCorp" in text
        assert "University of Zambia" in text
        assert "jane@example.com" in text
        assert "Google" not in text  # no fabricated employer

        # 11. Download PDF
        r = client.get(f"/api/v1/tailoring/cv/{v4['id']}/export/pdf", headers=auth)
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"
        assert len(r.content) > 100

        # Verify PDF content
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(r.content))
        pdf_text = "\n".join(page.extract_text() for page in reader.pages)
        assert "Jane Smith" in pdf_text or "Jane" in pdf_text
        assert "Python" in pdf_text
        assert "TechCorp" in pdf_text
        assert "University of Zambia" in pdf_text

    def test_failed_ai_does_not_corrupt_existing_cv(self, client: TestClient):
        """A failed edit request must not overwrite or corrupt the latest CV."""
        from backend.ai.cv_editor import CVEditorError

        token = _register_and_login(client)
        auth = _auth(token)
        cv_bytes = _make_docx(REALISTIC_CV)
        client.post(CV_UPLOAD_URL, headers=auth,
                    files={"file": ("cv.docx", cv_bytes, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=auth, json={"text": REALISTIC_JD})

        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=auth, json={})

        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_V1)):
            client.post(GENERATE_URL, headers=auth, json={})

        # Get the current version
        r = client.get(TAILORED_LATEST, headers=auth)
        assert r.status_code == 200
        current_version = r.json()["version_number"]

        # Attempt a failed edit
        with patch(PATCH_EDIT, new=AsyncMock(side_effect=CVEditorError("LLM down"))):
            r = client.post(EDIT_URL, headers=auth, json={"message": "Shorten my CV"})
        assert r.status_code == 422

        # Verify latest CV is unchanged
        r = client.get(TAILORED_LATEST, headers=auth)
        assert r.json()["version_number"] == current_version  # still v1, not corrupted

    def test_rejected_version_does_not_become_accepted(self, client: TestClient):
        """Rejecting one version must not affect the status of another."""
        token = _register_and_login(client)
        auth = _auth(token)
        cv_bytes = _make_docx(REALISTIC_CV)
        client.post(CV_UPLOAD_URL, headers=auth,
                    files={"file": ("cv.docx", cv_bytes, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=auth, json={"text": REALISTIC_JD})

        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=auth, json={})

        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_V1)):
            r = client.post(GENERATE_URL, headers=auth, json={})
        v1_id = r.json()["id"]

        # Accept v1
        client.patch(f"/api/v1/tailoring/cv/{v1_id}/status", headers=auth, json={"status": "accepted"})

        with patch(PATCH_EDIT, new=AsyncMock(return_value=MOCK_CV_V2)):
            r = client.post(EDIT_URL, headers=auth, json={"message": "Rewrite summary"})
        v2_id = r.json()["id"]

        # Reject v2
        client.patch(f"/api/v1/tailoring/cv/{v2_id}/status", headers=auth, json={"status": "rejected"})

        # v1 should still be accepted
        r = client.get(f"/api/v1/tailoring/cv/{v1_id}", headers=auth)
        assert r.json()["status"] == "accepted"
        # v2 should be rejected
        r = client.get(f"/api/v1/tailoring/cv/{v2_id}", headers=auth)
        assert r.json()["status"] == "rejected"

    def test_cross_user_cannot_access_exports(self, client: TestClient):
        """Another user cannot access CV versions or exports."""
        token1 = _register_and_login(client)
        auth1 = _auth(token1)
        cv_bytes = _make_docx(REALISTIC_CV)
        client.post(CV_UPLOAD_URL, headers=auth1,
                    files={"file": ("cv.docx", cv_bytes, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=auth1, json={"text": REALISTIC_JD})

        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=auth1, json={})

        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_V1)):
            r = client.post(GENERATE_URL, headers=auth1, json={})
        cv_id = r.json()["id"]

        # Second user
        token2 = _register_and_login(client)
        auth2 = _auth(token2)

        assert client.get(f"/api/v1/tailoring/cv/{cv_id}", headers=auth2).status_code == 404
        assert client.get(f"/api/v1/tailoring/cv/{cv_id}/export/docx", headers=auth2).status_code == 404
        assert client.get(f"/api/v1/tailoring/cv/{cv_id}/export/pdf", headers=auth2).status_code == 404
        assert client.patch(f"/api/v1/tailoring/cv/{cv_id}/status", headers=auth2, json={"status": "accepted"}).status_code == 404

    def test_original_cv_preserved_after_all_operations(self, client: TestClient):
        """The original uploaded CV document must remain unchanged after analysis, generation, and editing."""
        token = _register_and_login(client)
        auth = _auth(token)
        cv_bytes = _make_docx(REALISTIC_CV)
        r = client.post(CV_UPLOAD_URL, headers=auth,
                        files={"file": ("cv.docx", cv_bytes, DOCX_CT)})
        original_cv_id = r.json()["id"]
        original_text = r.json()["extracted_text"]

        client.post(JD_PASTE_URL, headers=auth, json={"text": REALISTIC_JD})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=auth, json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_V1)):
            client.post(GENERATE_URL, headers=auth, json={})
        with patch(PATCH_EDIT, new=AsyncMock(return_value=MOCK_CV_V2)):
            client.post(EDIT_URL, headers=auth, json={"message": "Rewrite summary"})

        # Original CV document should be unchanged
        r = client.get(f"/api/v1/tailoring/documents/{original_cv_id}", headers=auth)
        assert r.status_code == 200
        assert r.json()["extracted_text"] == original_text  # unchanged
