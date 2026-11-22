"""Regression tests for targeted CV Tailoring fixes.

Covers:
1. Stale-analysis safeguard (mismatched CV/JD document IDs).
2. Factual validation fixes (source="original" bypass, skill matching,
   placeholder detection, duration claims).
3. Structured CV generation prompt integrity.
4. DOCX/PDF export with complete content.

Uses the existing TestClient and shared test database from conftest.py.
All AI calls are mocked.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.services.cv_validator import CVValidator

# ── Constants ─────────────────────────────────────────────────────────────

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
CV_UPLOAD_URL = "/api/v1/tailoring/cv/upload"
JD_PASTE_URL = "/api/v1/tailoring/job-description/paste"
JD_UPLOAD_URL = "/api/v1/tailoring/job-description/upload"
ANALYZE_URL = "/api/v1/tailoring/analyze"
GENERATE_URL = "/api/v1/tailoring/cv/generate"
TAILORED_LATEST_URL = "/api/v1/tailoring/cv/tailored/latest"

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_CT = "application/pdf"

PATCH_JD = "backend.ai.jd_analyzer.JDAnalyzerService.analyze"
PATCH_MATCH = "backend.ai.cv_jd_matcher.CVJDMatcherService.match"
PATCH_RECS = "backend.ai.tailoring_advisor.TailoringAdvisorService.advise"
PATCH_REWRITE = "backend.ai.cv_rewriter.CVRewriterService.rewrite"

# ── Mock data ───────────────────────────────────────────────────────────────

MOCK_JD_ANALYSIS = {
    "job_title": "Corporate Business Development Executive",
    "employer": "Talent House Ltd",
    "location": "Lusaka, Zambia",
    "employment_type": "full-time",
    "responsibilities": ["Lead business development", "Manage client relationships"],
    "essential_qualifications": ["Bachelor's degree", "Business development experience"],
    "desirable_qualifications": ["MBA"],
    "required_experience_years": "3 years",
    "technical_skills": ["CRM", "Business Development", "Sales"],
    "soft_skills": ["Communication", "Negotiation"],
    "industry_keywords": ["business development", "client management"],
    "application_requirements": ["CV"],
    "summary": "Corporate business development role.",
}

MOCK_MATCH = {
    "matching_skills": [{"skill": "Business Development", "evidence": "React Digital Consulting", "jd_reference": "Business Development"}],
    "matching_experience": [{"requirement": "Business development experience", "evidence": "Managing Partner at React Digital Consulting", "strength": "strong"}],
    "matching_achievements": [],
    "missing_requirements": [{"requirement": "MBA", "type": "desirable", "note": "Not in CV"}],
    "missing_keywords": ["CRM"],
    "weak_sections": [],
    "overall_match_summary": "Strong business development match.",
}

MOCK_RECS = {
    "section_recommendations": [
        {"section": "Summary", "action": "rewrite", "reason": "Generic", "evidence": "HR and administration professional", "jd_reference": "Business Development Executive", "suggestion": "Target business development role"},
    ],
    "achievements_to_highlight": [],
    "information_to_request": [],
    "keyword_optimisation": [{"keyword": "business development", "action": "integrate", "note": "In JD and CV"}],
    "priority_actions": ["Rewrite summary for business development", "Reorganise experience"],
    "tailoring_summary": "Strong business development foundation. Address CRM gap.",
}

MOCK_CV_WITH_ALL_SECTIONS = {
    "sections": [
        {"heading": "MWANSA MATIMBA", "type": "contact", "content": "Mwansa Matimba\nLusaka, Zambia\n+260 977 190 580\nmwansamatimba@gmail.com", "items": [], "source": "original"},
        {"heading": "Professional Summary", "type": "summary", "content": "HR and business development professional with experience across recruitment, onboarding, and client consulting.", "items": [], "source": "rewritten"},
        {"heading": "Professional Experience", "type": "experience", "content": "", "items": [
            {"title": "Managing Partner", "company": "React Digital Consulting", "dates": "Jun 2019 - Present",
             "details": ["Run client consultations and deliver career counselling", "Develop business plans for SMEs", "Facilitate professional workshops"]},
            {"title": "HR Manager", "company": "eHemp House", "dates": "Jul 2020 - Jan 2021",
             "details": ["Supported HR administration and staff coordination", "Identified and documented organisational risks"]},
            {"title": "Associate / Consultant", "company": "Laura & Partners Consulting", "dates": "2015 - 2017",
             "details": ["Managed employment contracts and HRIS records", "Led recruitment and onboarding"]},
        ], "source": "rewritten"},
        {"heading": "Education", "type": "education", "content": "", "items": [
            {"qualification": "Bachelor of Entrepreneurship", "institution": "Mulungushi University", "dates": "2008 - 2013"},
        ], "source": "original"},
        {"heading": "Relevant Training and Certificates", "type": "certifications", "content": "", "items": [
            "Human Resources Management, Udemy (2021)",
            "Business Management, Tony Elumelu Foundation (2022)",
            "Become a Business Analyst Path, LinkedIn Learning (2022)",
        ], "source": "original"},
        {"heading": "Skills", "type": "skills", "content": "", "items": [
            "Microsoft Word (Advanced)", "Excel (Advanced)", "PowerPoint (Advanced)",
            "Outlook", "Teams", "Power BI", "SPSS",
        ], "source": "rewritten"},
        {"heading": "Languages", "type": "other", "content": "English (Fluent), Bemba (Proficient), Nyanja (Proficient), Tonga (Competent), Lozi (Adequate), French (Conversational)", "items": [], "source": "original"},
    ],
    "generation_notes": "Rewrote summary for business development focus. Preserved all employment entries, education, certificates, skills, and languages.",
}

# A CV with fabricated PPC/advertising content (the defect scenario)
MOCK_CV_WITH_FABRICATIONS = {
    "sections": [
        {"heading": "Contact", "type": "contact", "content": "Mwansa Matimba\nLusaka, Zambia", "items": [], "source": "original"},
        {"heading": "Professional Summary", "type": "summary", "content": "Paid search and PPC specialist with 3+ years of experience.", "items": [], "source": "rewritten"},
        {"heading": "Experience", "type": "experience", "content": "", "items": [
            {"title": "Paid Search Manager", "company": "Google Healthcare", "dates": "Jun 2019 - Present",
             "details": ["Managed Google Ads campaigns with 500% ROI", "3+ years of paid-search experience", "Microsoft Ads certified"]},
        ], "source": "original"},
    ],
    "generation_notes": "Fabricated PPC experience.",
}

# A CV with placeholder values
MOCK_CV_WITH_PLACEHOLDERS = {
    "sections": [
        {"heading": "Contact", "type": "contact", "content": "Mwansa Matimba\nLusaka", "items": [], "source": "original"},
        {"heading": "Experience", "type": "experience", "content": "", "items": [
            {"title": "Job title", "company": "Company", "dates": "Dates", "details": ["Did things"]},
        ], "source": "rewritten"},
    ],
    "generation_notes": "Contains placeholders.",
}

ORIGINAL_CV_TEXT = """
Mwansa Matimba
Human Resources Manager
Lusaka, Zambia

Professional Experience:
- Managing Partner at React Digital Consulting (Jun 2019 - Present)
  Run client consultations and deliver career counselling
  Develop business plans for SMEs
- HR Manager at eHemp House (Jul 2020 - Jan 2021)
  Supported HR administration and staff coordination
- Associate / Consultant at Laura & Partners Consulting (2015 - 2017)
  Managed employment contracts and HRIS records

Education:
Bachelor of Entrepreneurship, Mulungushi University (2008 - 2013)

Certifications:
Human Resources Management, Udemy (2021)
Business Management, Tony Elumelu Foundation (2022)

Skills: Microsoft Word (Advanced), Excel (Advanced), PowerPoint (Advanced), Outlook, Teams, Power BI, SPSS
Languages: English, Bemba, Nyanja, Tonga, Lozi, French
"""

# ── Helpers ────────────────────────────────────────────────────────────────

def _email() -> str:
    return f"fixes_{uuid.uuid4().hex[:8]}@example.com"

def _register_and_login(client: TestClient) -> str:
    email = _email()
    pw = "TestPassword123!"
    r = client.post(REGISTER_URL, json={"email": email, "password": pw, "full_name": "Fixes Test"})
    assert r.status_code == 201, r.text
    r = client.post(LOGIN_URL, json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]

def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}

def _make_docx(text: str) -> bytes:
    from docx import Document
    doc = Document()
    for line in text.strip().split("\n"):
        doc.add_paragraph(line.strip())
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── Tests ──────────────────────────────────────────────────────────────────

class TestStaleAnalysisSafeguard:
    """Tests for the stale-analysis safeguard in generate_tailored_cv."""

    def test_generate_fails_when_cv_changed_after_analysis(self, client: TestClient):
        """A new CV uploaded after analysis must block generation.

        We delete the original CV (soft-delete) and upload a new one so
        that get_latest_cv is guaranteed to return the new CV, regardless
        of timestamp precision.
        """
        token = _register_and_login(client)
        # Upload CV_A
        docx_a = _make_docx("First CV version with Python experience.")
        r = client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv_a.docx", docx_a, DOCX_CT)})
        assert r.status_code == 201
        cv_a_id = r.json()["id"]
        # Paste JD
        r = client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Software Engineer role at TechCorp."})
        assert r.status_code == 201
        # Run analysis (mocked)
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            r = client.post(ANALYZE_URL, headers=_auth(token), json={})
            assert r.status_code == 201
        # Soft-delete CV_A so get_latest_cv will not return it
        r = client.delete(f"/api/v1/tailoring/documents/{cv_a_id}", headers=_auth(token))
        assert r.status_code == 204
        # Upload CV_B (now the only visible CV)
        docx_b = _make_docx("Second CV version with different content.")
        r = client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv_b.docx", docx_b, DOCX_CT)})
        assert r.status_code == 201
        # Generate must fail because analysis was run against CV_A, not CV_B
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        assert r.status_code == 422
        assert "different cv" in r.json()["detail"].lower()

    def test_generate_fails_when_jd_changed_after_analysis(self, client: TestClient):
        """A new JD pasted after analysis must block generation.

        We delete the original JD (soft-delete) and paste a new one so
        that get_latest_job_description returns the new JD.
        """
        token = _register_and_login(client)
        # Upload CV
        docx = _make_docx("CV with Python and FastAPI experience.")
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        # Paste JD_A
        r = client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Senior Python Developer at TechCorp."})
        assert r.status_code == 201
        jd_a_id = r.json()["id"]
        # Run analysis
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        # Soft-delete JD_A
        r = client.delete(f"/api/v1/tailoring/documents/{jd_a_id}", headers=_auth(token))
        assert r.status_code == 204
        # Paste JD_B (now the only visible JD)
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "HR Manager at Talent House Ltd."})
        # Generate must fail
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        assert r.status_code == 422
        assert "different job description" in r.json()["detail"].lower()

    def test_generate_succeeds_when_analysis_matches_current_docs(self, client: TestClient):
        """Happy path: analysis matches current CV and JD, generation works."""
        token = _register_and_login(client)
        docx = _make_docx("CV with business development experience.")
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business Development Executive role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        assert r.status_code == 201
        assert r.json()["version_number"] == 1


class TestValidatorSourceBypass:
    """Tests that source='original' no longer bypasses validation for experience."""

    def test_experience_with_source_original_is_validated(self):
        """Experience sections marked source='original' must still be checked."""
        sections = [
            {"heading": "Experience", "type": "experience",
             "items": [{"title": "Paid Search Manager", "company": "Google Healthcare",
                        "dates": "Jun 2019 - Present",
                        "details": ["Managed Google Ads campaigns"]}],
             "source": "original"},
        ]
        result = CVValidator.validate(sections, ORIGINAL_CV_TEXT)
        # "Google Healthcare" is not in the original CV — must be flagged
        assert not result.is_valid
        assert any("Google Healthcare" in c["claim"] for c in result.unsupported_claims)

    def test_contact_still_trusted(self):
        """Contact sections should always pass."""
        sections = [
            {"heading": "Contact", "type": "contact",
             "content": "Mwansa Matimba\nLusaka, Zambia", "source": "original"},
        ]
        result = CVValidator.validate(sections, ORIGINAL_CV_TEXT)
        assert result.is_valid


class TestValidatorSkillMatching:
    """Tests that skill matching requires at least 2 token matches."""

    def test_google_ads_rejected_with_only_google_in_original(self):
        """'Google Ads' must not pass when only 'Google' (in different context) is in original."""
        orig = "Used Google Analytics for website tracking. Microsoft Office skills."
        sections = [{"heading": "Skills", "type": "skills",
                     "items": ["Google Ads", "Microsoft Ads"], "source": "rewritten"}]
        result = CVValidator.validate(sections, orig)
        # Both should be needs_confirmation, not reasonable_rewording
        google_finding = [f for f in result.findings if "Google Ads" in f.get("claim", "")]
        assert len(google_finding) == 1
        assert google_finding[0]["classification"] == "needs_confirmation"

    def test_microsoft_ads_rejected_with_only_microsoft_office(self):
        """'Microsoft Ads' must not pass when only 'Microsoft Office' is in original."""
        orig = "Microsoft Office (Advanced), Excel, PowerPoint."
        sections = [{"heading": "Skills", "type": "skills",
                     "items": ["Microsoft Ads"], "source": "rewritten"}]
        result = CVValidator.validate(sections, orig)
        ms_finding = [f for f in result.findings if "Microsoft Ads" in f.get("claim", "")]
        assert len(ms_finding) == 1
        assert ms_finding[0]["classification"] == "needs_confirmation"

    def test_real_skill_still_supported(self):
        """A skill present in the original CV must still pass."""
        sections = [{"heading": "Skills", "type": "skills",
                     "items": ["Excel (Advanced)"], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV_TEXT)
        assert result.is_valid


class TestValidatorPlaceholderDetection:
    """Tests that placeholder values are detected and rejected."""

    def test_placeholder_job_title_rejected(self):
        sections = [
            {"heading": "Experience", "type": "experience",
             "items": [{"title": "Job title", "company": "Real Company",
                        "dates": "2020-2024", "details": ["Real work"]}],
             "source": "rewritten"},
        ]
        result = CVValidator.validate(sections, "Real Company 2020-2024 Real work")
        assert not result.is_valid
        assert any("Placeholder" in c["detail"] for c in result.unsupported_claims)

    def test_placeholder_company_rejected(self):
        sections = [
            {"heading": "Experience", "type": "experience",
             "items": [{"title": "Real Title", "company": "Company",
                        "dates": "2020-2024", "details": ["Real work"]}],
             "source": "rewritten"},
        ]
        result = CVValidator.validate(sections, "Real Title 2020-2024 Real work")
        assert not result.is_valid

    def test_placeholder_dates_rejected(self):
        sections = [
            {"heading": "Experience", "type": "experience",
             "items": [{"title": "Real Title", "company": "Real Company",
                        "dates": "Dates", "details": ["Real work"]}],
             "source": "rewritten"},
        ]
        result = CVValidator.validate(sections, "Real Title Real Company 2020-2024 Real work")
        assert not result.is_valid

    def test_placeholder_certification_rejected(self):
        sections = [
            {"heading": "Certifications", "type": "certifications",
             "items": ["[Your Certification Here]"], "source": "rewritten"},
        ]
        result = CVValidator.validate(sections, "Some original text")
        assert not result.is_valid

    def test_placeholder_skill_rejected(self):
        sections = [
            {"heading": "Skills", "type": "skills",
             "items": ["N/A"], "source": "rewritten"},
        ]
        result = CVValidator.validate(sections, "Some original text with skills")
        assert not result.is_valid


class TestValidatorDurationClaims:
    """Tests that fabricated experience-duration claims are flagged."""

    def test_fabricated_paid_search_duration_flagged(self):
        """'3+ years of paid-search experience' must be flagged when 'paid-search' is not in original."""
        sections = [
            {"heading": "Experience", "type": "experience",
             "items": [{"title": "HR Manager", "company": "eHemp House",
                        "dates": "Jul 2020 - Jan 2021",
                        "details": ["3+ years of paid-search experience"]}],
             "source": "rewritten"},
        ]
        result = CVValidator.validate(sections, ORIGINAL_CV_TEXT)
        # The duration claim references 'paid-search' which is not in the original
        paid_search_findings = [
            f for f in result.findings + result.warnings
            if "paid-search" in f.get("detail", "").lower()
        ]
        assert len(paid_search_findings) >= 1

    def test_real_duration_not_flagged(self):
        """A duration claim referencing a real domain should not be flagged."""
        sections = [
            {"heading": "Experience", "type": "experience",
             "items": [{"title": "HR Manager", "company": "eHemp House",
                        "dates": "Jul 2020 - Jan 2021",
                        "details": ["3 years of HR administration experience"]}],
             "source": "rewritten"},
        ]
        result = CVValidator.validate(sections, ORIGINAL_CV_TEXT)
        # "HR administration" IS in the original CV text, so no issue should
        # be raised about the domain keyword.
        bd_findings = [
            f for f in result.warnings
            if "hr administration" in f.get("detail", "").lower()
            and "not found" in f.get("detail", "").lower()
        ]
        assert len(bd_findings) == 0


class TestValidatorFabrications:
    """Integration: fabricated PPC/advertising content must be caught."""

    def test_fabricated_ppc_cv_has_unsupported_claims(self):
        """The specific fabrication scenario from the defect report."""
        result = CVValidator.validate(
            MOCK_CV_WITH_FABRICATIONS["sections"],
            ORIGINAL_CV_TEXT,
        )
        assert not result.is_valid
        # "Google Healthcare" as employer must be unsupported
        employer_claims = [c for c in result.unsupported_claims if "Google" in c["claim"]]
        assert len(employer_claims) >= 1


class TestStructuredCVPreservation:
    """Tests that the structured CV generation preserves all candidate information."""

    def test_generated_cv_has_all_employment_entries(self, client: TestClient):
        """All 3 employment entries from the mock must appear in the generated CV."""
        token = _register_and_login(client)
        docx = _make_docx("CV with business development experience.")
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business Development Executive role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        assert r.status_code == 201
        sections = r.json()["content"]["sections"]
        # Check all 3 employers present
        exp_section = [s for s in sections if s["type"] == "experience"][0]
        employers = [item.get("company", "") for item in exp_section["items"]]
        assert "React Digital Consulting" in employers
        assert "eHemp House" in employers
        assert "Laura & Partners Consulting" in employers

    def test_generated_cv_has_education(self, client: TestClient):
        """Education section must be present with institution and qualification."""
        token = _register_and_login(client)
        docx = _make_docx("CV with education at Mulungushi University.")
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        sections = r.json()["content"]["sections"]
        edu = [s for s in sections if s["type"] == "education"]
        assert len(edu) == 1
        assert "Mulungushi University" in str(edu[0]["items"])

    def test_generated_cv_has_certificates(self, client: TestClient):
        """Certificate section must be present with all entries."""
        token = _register_and_login(client)
        docx = _make_docx("CV with certificates.")
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        sections = r.json()["content"]["sections"]
        certs = [s for s in sections if s["type"] == "certifications"]
        assert len(certs) == 1
        cert_items = certs[0]["items"]
        assert any("Udemy" in str(c) for c in cert_items)
        assert any("Tony Elumelu" in str(c) for c in cert_items)
        assert any("LinkedIn Learning" in str(c) for c in cert_items)

    def test_generated_cv_has_skills_and_languages(self, client: TestClient):
        """Skills and languages must be present in the generated CV."""
        token = _register_and_login(client)
        docx = _make_docx("CV with skills and languages.")
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        sections = r.json()["content"]["sections"]
        skills = [s for s in sections if s["type"] == "skills"]
        assert len(skills) == 1
        assert "Excel (Advanced)" in skills[0]["items"]
        # Languages section
        other = [s for s in sections if s["type"] == "other"]
        assert len(other) >= 1
        assert "English" in other[0]["content"]

    def test_generated_cv_has_validation_results(self, client: TestClient):
        """The generated CV must contain validation results."""
        token = _register_and_login(client)
        docx = _make_docx("CV with experience.")
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        content = r.json()["content"]
        assert "validation" in content
        assert "is_valid" in content["validation"]


class TestExportWithCompleteContent:
    """Tests that DOCX and PDF export contain all sections."""

    def test_docx_contains_all_employers(self, client: TestClient):
        token = _register_and_login(client)
        docx = _make_docx(ORIGINAL_CV_TEXT)
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        cv_id = r.json()["id"]
        r = client.get(f"/api/v1/tailoring/cv/{cv_id}/export/docx", headers=_auth(token))
        assert r.status_code == 200
        from docx import Document
        doc = Document(io.BytesIO(r.content))
        text = "\n".join(p.text for p in doc.paragraphs)
        assert "React Digital Consulting" in text
        assert "eHemp House" in text
        assert "Laura & Partners Consulting" in text
        assert "Mulungushi University" in text

    def test_pdf_contains_all_employers(self, client: TestClient):
        token = _register_and_login(client)
        docx = _make_docx(ORIGINAL_CV_TEXT)
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        cv_id = r.json()["id"]
        r = client.get(f"/api/v1/tailoring/cv/{cv_id}/export/pdf", headers=_auth(token))
        assert r.status_code == 200
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(r.content))
        text = "\n".join(page.extract_text() for page in reader.pages)
        assert "React Digital Consulting" in text
        assert "eHemp House" in text
        assert "Mulungushi University" in text


# ── Regression tests for F1–F6 and design-review corrections ───────────────


class TestApplyEditValidation:
    """F1: Conversational edits must run CVValidator and store a validation result."""

    def test_apply_edit_saves_validation_result(self, client: TestClient):
        """After a conversational edit the new version must have a 'validation' key."""
        token = _register_and_login(client)
        docx = _make_docx(ORIGINAL_CV_TEXT)
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            client.post(GENERATE_URL, headers=_auth(token), json={})

        # Fabricated content in the edit response — validator must catch it.
        fabricated_edit = {
            "sections": [
                {"heading": "Contact", "type": "contact",
                 "content": "Mwansa Matimba\nLusaka", "items": [], "source": "original"},
                {"heading": "Experience", "type": "experience", "content": "", "items": [
                    {"title": "Paid Search Manager", "company": "Google Healthcare",
                     "dates": "Jun 2019 - Present",
                     "details": ["Managed Google Ads campaigns"]},
                ], "source": "rewritten"},
            ],
            "edit_summary": "Changed experience to PPC.",
            "needs_clarification": False,
            "section_explanations": [],
        }
        patch_editor = "backend.ai.cv_editor.CVEditorService.apply_edit"
        with patch(patch_editor, new=AsyncMock(return_value=fabricated_edit)):
            r = client.post("/api/v1/tailoring/cv/edit", headers=_auth(token),
                            json={"message": "Change my experience to PPC."})
        assert r.status_code == 201
        content = r.json()["content"]
        assert "validation" in content, "apply_edit must store a validation result"
        assert "is_valid" in content["validation"]
        assert "skipped" in content["validation"]
        # Fabricated employer must be caught
        assert content["validation"]["is_valid"] is False

    def test_apply_edit_valid_edit_has_is_valid_true(self, client: TestClient):
        """A conversational edit that keeps valid content must produce is_valid=True."""
        token = _register_and_login(client)
        docx = _make_docx(ORIGINAL_CV_TEXT)
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            client.post(GENERATE_URL, headers=_auth(token), json={})

        valid_edit = dict(MOCK_CV_WITH_ALL_SECTIONS)
        valid_edit["edit_summary"] = "Minor wording tweak."
        valid_edit["needs_clarification"] = False
        valid_edit["section_explanations"] = []
        patch_editor = "backend.ai.cv_editor.CVEditorService.apply_edit"
        with patch(patch_editor, new=AsyncMock(return_value=dict(valid_edit))):
            r = client.post("/api/v1/tailoring/cv/edit", headers=_auth(token),
                            json={"message": "Improve summary wording."})
        assert r.status_code == 201
        content = r.json()["content"]
        assert content["validation"]["is_valid"] is True


class TestVisualEditValidation:
    """F2: Visual edits must run CVValidator and store a validation result."""

    def test_visual_edit_saves_validation_result(self, client: TestClient):
        """After a visual edit the new version must have a 'validation' key."""
        token = _register_and_login(client)
        docx = _make_docx(ORIGINAL_CV_TEXT)
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r_gen = client.post(GENERATE_URL, headers=_auth(token), json={})
        cv_id = r_gen.json()["id"]

        # Visual edit with a fabricated employer
        fabricated_sections = [
            {"heading": "Contact", "type": "contact",
             "content": "Mwansa Matimba\nLusaka", "items": [], "source": "original"},
            {"heading": "Experience", "type": "experience", "content": "", "items": [
                {"title": "Manager", "company": "Accenture Digital Africa",
                 "dates": "2020 - 2022", "details": ["Consulting work"]},
            ], "source": "rewritten"},
        ]
        r = client.post(f"/api/v1/tailoring/cv/{cv_id}/visual-edit",
                        headers=_auth(token),
                        json={"sections": fabricated_sections})
        assert r.status_code == 201
        content = r.json()["content"]
        assert "validation" in content
        assert "is_valid" in content["validation"]
        assert "skipped" in content["validation"]
        assert content["validation"]["is_valid"] is False

    def test_visual_edit_valid_sections_pass_validation(self, client: TestClient):
        """Visual edit with unchanged valid sections produces is_valid=True."""
        token = _register_and_login(client)
        docx = _make_docx(ORIGINAL_CV_TEXT)
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r_gen = client.post(GENERATE_URL, headers=_auth(token), json={})
        cv_id = r_gen.json()["id"]

        # Alter one field slightly to avoid the "no changes" guard
        updated_sections = [
            dict(s) for s in MOCK_CV_WITH_ALL_SECTIONS["sections"]
        ]
        updated_sections[1] = dict(updated_sections[1])
        updated_sections[1]["content"] = updated_sections[1]["content"] + " (updated)"
        r = client.post(f"/api/v1/tailoring/cv/{cv_id}/visual-edit",
                        headers=_auth(token),
                        json={"sections": updated_sections})
        assert r.status_code == 201
        content = r.json()["content"]
        assert content["validation"]["is_valid"] is True


class TestExportValidationEnforcement:
    """F3: Export endpoints must enforce validation status."""

    def _generate_cv(self, client: TestClient, cv_text: str, mock_cv: dict) -> tuple[str, str]:
        """Helper: upload cv_text, run analysis, generate mock_cv; return (token, cv_id)."""
        token = _register_and_login(client)
        docx = _make_docx(cv_text)
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=mock_cv)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        return token, r.json()["id"]

    def test_export_docx_blocked_for_invalid_cv(self, client: TestClient):
        """DOCX export must return 422 when is_valid=False."""
        # Minimal CV text gives no employer evidence → MOCK_CV_WITH_FABRICATIONS fails validation
        token, cv_id = self._generate_cv(client, "Some candidate text.", MOCK_CV_WITH_FABRICATIONS)
        r = client.get(f"/api/v1/tailoring/cv/{cv_id}/export/docx", headers=_auth(token))
        assert r.status_code == 422

    def test_export_pdf_blocked_for_invalid_cv(self, client: TestClient):
        """PDF export must return 422 when is_valid=False."""
        token, cv_id = self._generate_cv(client, "Some candidate text.", MOCK_CV_WITH_FABRICATIONS)
        r = client.get(f"/api/v1/tailoring/cv/{cv_id}/export/pdf", headers=_auth(token))
        assert r.status_code == 422

    def test_export_allowed_for_warnings_only_cv(self, client: TestClient):
        """Export must succeed when is_valid=True even if warnings are present."""
        token, cv_id = self._generate_cv(client, ORIGINAL_CV_TEXT, MOCK_CV_WITH_ALL_SECTIONS)
        r = client.get(f"/api/v1/tailoring/cv/{cv_id}/export/docx", headers=_auth(token))
        assert r.status_code == 200


class TestExportSkippedValidationSentinel:
    """F1 correction: skipped=True must block export with HTTP 422."""

    def test_export_blocked_when_validation_skipped(self, client: TestClient):
        """A CV whose validation was skipped (technical error) must be blocked."""
        token = _register_and_login(client)
        docx = _make_docx(ORIGINAL_CV_TEXT)
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        # Force validation to raise so the skip-notice path fires
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            with patch("backend.services.cv_validator.CVValidator.validate",
                       side_effect=RuntimeError("forced validation error")):
                r = client.post(GENERATE_URL, headers=_auth(token), json={})
        assert r.status_code == 201
        content = r.json()["content"]
        assert content["validation"]["skipped"] is True
        assert content["validation"]["is_valid"] is True  # fail-open preserved on save

        cv_id = r.json()["id"]
        # Export must be blocked — skipped validation is unverified content.
        r = client.get(f"/api/v1/tailoring/cv/{cv_id}/export/docx", headers=_auth(token))
        assert r.status_code == 422


class TestSummaryQuantFigures:
    """F4: Unrecognised quantified figures in summary must set is_valid=False."""

    def test_summary_fabricated_figure_sets_is_valid_false(self):
        """A percent figure absent from the original must block export."""
        sections = [
            {"heading": "Summary", "type": "summary",
             "content": "Increased revenue by 300%.", "items": [], "source": "rewritten"},
        ]
        result = CVValidator.validate(sections, ORIGINAL_CV_TEXT)
        # 300% is not in ORIGINAL_CV_TEXT
        assert result.is_valid is False

    def test_summary_supported_figure_still_passes(self):
        """A figure present verbatim in the original must still pass."""
        orig = "Reduced costs by 40%."
        sections = [
            {"heading": "Summary", "type": "summary",
             "content": "Reduced costs by 40%.", "items": [], "source": "rewritten"},
        ]
        result = CVValidator.validate(sections, orig)
        assert result.is_valid is True


class TestEducationTokenOverlap:
    """F5: Education institution must use token-overlap instead of first-word match."""

    def test_first_word_only_match_is_rejected(self):
        """An institution sharing only its first word with the original must fail."""
        # 'National' appears in original context but 'Business College' does not
        orig = "National Road Authority 2019 - 2022. University of Zambia 2014-2018."
        sections = [{"heading": "Education", "type": "education", "items": [
            {"institution": "National Business College",
             "qualification": "Diploma in Management", "dates": "2016-2018"},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, orig)
        # 'national' alone must not be enough — 'business' and 'college' are absent
        assert result.is_valid is False

    def test_verbatim_institution_still_passes(self, client: TestClient = None):
        """An institution present verbatim in the original must still pass."""
        sections = [{"heading": "Education", "type": "education", "items": [
            {"institution": "Mulungushi University",
             "qualification": "Bachelor of Entrepreneurship", "dates": "2008-2013"},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV_TEXT)
        assert result.is_valid is True

    def test_completely_fabricated_institution_rejected(self):
        """An institution with no token overlap must be unsupported."""
        sections = [{"heading": "Education", "type": "education", "items": [
            {"institution": "Harvard Business School",
             "qualification": "MBA", "dates": "2010-2012"},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV_TEXT)
        assert result.is_valid is False


class TestRevertRevalidation:
    """F6: Revert must run fresh validation, not inherit the old result."""

    def test_revert_stores_fresh_validation(self, client: TestClient):
        """Reverted version must have a 'validation' key with fresh results."""
        token = _register_and_login(client)
        docx = _make_docx(ORIGINAL_CV_TEXT)
        client.post(CV_UPLOAD_URL, headers=_auth(token), files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business role."})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_CV_WITH_ALL_SECTIONS)):
            r1 = client.post(GENERATE_URL, headers=_auth(token), json={})
        v1_number = r1.json()["version_number"]

        # Create a second version via edit
        valid_edit = dict(MOCK_CV_WITH_ALL_SECTIONS)
        valid_edit["edit_summary"] = "Minor wording tweak."
        valid_edit["needs_clarification"] = False
        valid_edit["section_explanations"] = []
        patch_editor = "backend.ai.cv_editor.CVEditorService.apply_edit"
        with patch(patch_editor, new=AsyncMock(return_value=dict(valid_edit))):
            client.post("/api/v1/tailoring/cv/edit", headers=_auth(token),
                        json={"message": "Improve summary."})

        # Revert to v1
        r_revert = client.post("/api/v1/tailoring/cv/revert",
                               headers=_auth(token),
                               json={"version_number": v1_number})
        assert r_revert.status_code == 201
        reverted_content = r_revert.json()["content"]
        assert "validation" in reverted_content, "Reverted version must have validation key"
        assert "is_valid" in reverted_content["validation"]
        assert "skipped" in reverted_content["validation"]


class TestValidationSkippedSentinelInToDict:
    """Design-review correction: CVValidationResult.to_dict() must include skipped=False."""

    def test_to_dict_includes_skipped_false(self):
        sections = [{"heading": "Contact", "type": "contact",
                     "content": "Jane Smith", "source": "original"}]
        result = CVValidator.validate(sections, ORIGINAL_CV_TEXT)
        d = result.to_dict()
        assert "skipped" in d
        assert d["skipped"] is False


# ── Regression tests for F1/F2/F3 corrections ──────────────────────────────

import datetime as _datetime  # local alias to avoid shadowing stdlib in fixtures


class TestExportEnforcementCorrected:
    """Five focused tests for the three corrections.

    1. Export blocked when validation is skipped (F1).
    2. Genuine legacy version (pre-enforcement) exports freely (F3).
    3. New version without validation record is blocked (F3).
    4. Conversational edit uses generation-time CV, not latest upload (F2).
    5. Revert validation uses the generation-time CV, not latest upload (F2/F6).
    """

    # ── helpers ──────────────────────────────────────────────────────────────

    def _setup_valid_cv(self, client: TestClient) -> tuple[str, str]:
        """Upload ORIGINAL_CV_TEXT, run analysis, generate CV. Return (token, cv_id)."""
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
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        assert r.status_code == 201
        return token, r.json()["id"]

    # ── Test 1: skipped validation blocks export ──────────────────────────────

    def test_export_blocked_when_validation_skipped(self, client: TestClient):
        """F1: A CV with skipped=True must return 422 on export (not 200)."""
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
                       side_effect=RuntimeError("forced validation crash")):
                r = client.post(GENERATE_URL, headers=_auth(token), json={})
        assert r.status_code == 201
        assert r.json()["content"]["validation"]["skipped"] is True

        cv_id = r.json()["id"]
        for endpoint in ("docx", "pdf"):
            resp = client.get(
                f"/api/v1/tailoring/cv/{cv_id}/export/{endpoint}",
                headers=_auth(token),
            )
            assert resp.status_code == 422, (
                f"Expected 422 for {endpoint} export of skipped-validation CV, "
                f"got {resp.status_code}"
            )

    # ── Test 2: genuine legacy version exports freely ─────────────────────────

    def test_legacy_version_without_validation_key_exports_freely(
        self, client: TestClient
    ):
        """F3 (endpoint-level): A TailoredCV row created before enforcement
        (before 2026-11-17T14:46:54Z) with no ``validation`` key must return
        200 on both DOCX and PDF export.

        Strategy:
          1. Generate a real CV through the HTTP pipeline (gets a valid
             validation record and a current created_at).
          2. Use a direct async DB write to remove the ``validation`` key
             from content and backdate ``created_at`` to 2026-11-16T12:00Z —
             one day and ~2.75 hours before the enforcement cutoff.
          3. Assert that both DOCX and PDF export endpoints return 200.
        """
        import asyncio as _asyncio
        import uuid as _uuid
        import copy
        from backend.database.session import async_session_factory
        from backend.models.tailored_cv import TailoredCV as TailoredCVModel

        token, cv_id = self._setup_valid_cv(client)

        # Backdate to 2026-11-16 12:00 UTC — clearly before the enforcement
        # cutoff of 2026-11-17 14:46:54 UTC.
        pre_enforcement = _datetime.datetime(2026, 11, 16, 12, 0, 0,
                                              tzinfo=_datetime.timezone.utc)

        async def _backdate():
            async with async_session_factory() as session:
                row = await session.get(TailoredCVModel, _uuid.UUID(cv_id))
                if row is not None:
                    new_content = copy.deepcopy(row.content)
                    new_content.pop("validation", None)
                    row.content = new_content
                    row.created_at = pre_enforcement
                    await session.commit()

        _asyncio.run(_backdate())

        # Both export endpoints must succeed for a genuine legacy row.
        for endpoint in ("docx", "pdf"):
            r = client.get(
                f"/api/v1/tailoring/cv/{cv_id}/export/{endpoint}",
                headers=_auth(token),
            )
            assert r.status_code == 200, (
                f"Legacy CV (created_at={pre_enforcement.isoformat()}, no "
                f"validation key) must export freely on {endpoint}, "
                f"got {r.status_code}: {r.text}"
            )

    def test_new_version_without_validation_key_is_blocked(
        self, client: TestClient
    ):
        """F3 (endpoint-level): A TailoredCV row created on or after the
        enforcement cutoff (2026-11-17T14:46:54Z) with no ``validation`` key
        must return HTTP 422 for both DOCX and PDF export.

        Strategy:
          1. Generate a real CV through the HTTP pipeline.
          2. Set ``created_at`` explicitly to 2026-11-17T15:00:00Z — 13
             minutes after the enforcement cutoff — and strip the
             ``validation`` key from content.
          3. Assert that both export endpoints return 422.
        """
        import asyncio as _asyncio
        import uuid as _uuid
        import copy
        from backend.database.session import async_session_factory
        from backend.models.tailored_cv import TailoredCV as TailoredCVModel

        token, cv_id = self._setup_valid_cv(client)

        # Set to a deterministic post-cutoff timestamp rather than relying on
        # "now is always after the cutoff" — makes the test self-documenting
        # and immune to clock drift.
        post_enforcement = _datetime.datetime(2026, 11, 17, 15, 0, 0,
                                               tzinfo=_datetime.timezone.utc)

        async def _set_post_enforcement_and_strip():
            async with async_session_factory() as session:
                row = await session.get(TailoredCVModel, _uuid.UUID(cv_id))
                if row is not None:
                    new_content = copy.deepcopy(row.content)
                    new_content.pop("validation", None)
                    row.content = new_content
                    row.created_at = post_enforcement
                    await session.commit()

        _asyncio.run(_set_post_enforcement_and_strip())

        # Both export endpoints must block.
        for endpoint in ("docx", "pdf"):
            r = client.get(
                f"/api/v1/tailoring/cv/{cv_id}/export/{endpoint}",
                headers=_auth(token),
            )
            assert r.status_code == 422, (
                f"Post-enforcement CV (created_at={post_enforcement.isoformat()}, "
                f"no validation key) must return 422 on {endpoint} export, "
                f"got {r.status_code}"
            )
            assert "no validation record" in r.json()["detail"].lower(), (
                f"422 detail should mention 'no validation record', "
                f"got: {r.json()['detail']}"
            )

    # ── Test 4: conversational edit uses generation-time CV after re-upload ───

    def test_apply_edit_uses_generation_time_cv_after_re_upload(
        self, client: TestClient
    ):
        """F2: After uploading a second CV, apply_edit must validate against the
        original generation CV, not the new upload.

        Setup:
          CV-A = ORIGINAL_CV_TEXT (contains 'eHemp House')
          CV-B = text with no employer evidence

        After generating with CV-A, uploading CV-B, and running a conversational
        edit, the validation result on the new version must accept 'eHemp House'
        (supported by CV-A) rather than flagging it as unsupported (which it
        would be if the validator used CV-B).
        """
        token, _ = self._setup_valid_cv(client)

        # Upload a new CV-B that has no employer content
        docx_b = _make_docx("Candidate curriculum vitae.")
        client.post(CV_UPLOAD_URL, headers=_auth(token),
                    files={"file": ("cv_b.docx", docx_b, DOCX_CT)})

        # Conversational edit that returns the same valid content as the original
        valid_edit = dict(MOCK_CV_WITH_ALL_SECTIONS)
        valid_edit["edit_summary"] = "Minor wording tweak."
        valid_edit["needs_clarification"] = False
        valid_edit["section_explanations"] = []

        with patch("backend.ai.cv_editor.CVEditorService.apply_edit",
                   new=AsyncMock(return_value=dict(valid_edit))):
            r = client.post(
                "/api/v1/tailoring/cv/edit",
                headers=_auth(token),
                json={"message": "Improve summary wording."},
            )
        assert r.status_code == 201
        validation = r.json()["content"]["validation"]
        # If the validator used CV-B (no employer text), eHemp House would be
        # flagged unsupported.  If it used CV-A, eHemp House passes.
        assert validation["is_valid"] is True, (
            "apply_edit validation must use CV-A (the generation-time source), "
            "not CV-B (the most recently uploaded CV). "
            f"unsupported_claims: {validation.get('unsupported_claims')}"
        )

    # ── Test 5: revert validation uses generation-time CV after re-upload ─────

    def test_revert_uses_generation_time_cv_after_re_upload(
        self, client: TestClient
    ):
        """F2/F6: After uploading a second CV, revert must validate against the
        target version's generation CV, not the new upload.

        Same CV-A / CV-B split as Test 4.  After generating v1 with CV-A,
        uploading CV-B, and reverting to v1, the reverted copy must have
        is_valid=True because its employers are supported by CV-A.
        """
        token, _ = self._setup_valid_cv(client)
        v1_number = client.get(
            TAILORED_LATEST_URL, headers=_auth(token)
        ).json()["version_number"]

        # Create a second version via visual edit so there is something to revert FROM
        r_latest = client.get(TAILORED_LATEST_URL, headers=_auth(token))
        cv_id = r_latest.json()["id"]
        updated_sections = list(MOCK_CV_WITH_ALL_SECTIONS["sections"])
        updated_sections = [dict(s) for s in updated_sections]
        updated_sections[1] = dict(updated_sections[1])
        updated_sections[1]["content"] = updated_sections[1]["content"] + " (v2)"
        client.post(
            f"/api/v1/tailoring/cv/{cv_id}/visual-edit",
            headers=_auth(token),
            json={"sections": updated_sections},
        )

        # Upload CV-B with no employer evidence
        docx_b = _make_docx("Candidate curriculum vitae.")
        client.post(CV_UPLOAD_URL, headers=_auth(token),
                    files={"file": ("cv_b.docx", docx_b, DOCX_CT)})

        # Revert to v1
        r_revert = client.post(
            "/api/v1/tailoring/cv/revert",
            headers=_auth(token),
            json={"version_number": v1_number},
        )
        assert r_revert.status_code == 201
        validation = r_revert.json()["content"]["validation"]
        assert validation["is_valid"] is True, (
            "revert_to_version must validate against CV-A (v1's source), "
            "not CV-B (the latest upload). "
            f"unsupported_claims: {validation.get('unsupported_claims')}"
        )


# ── Application-package export enforcement + empty-source fail-closed ──────

class TestApplicationPackageExportEnforcement:
    """The /application-package/export endpoint must enforce the same
    validation policy as the standalone DOCX/PDF export routes."""

    def _setup_valid_cv(self, client: TestClient) -> tuple[str, str]:
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
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        assert r.status_code == 201
        return token, r.json()["id"]

    def test_application_package_blocked_for_invalid_cv(self, client: TestClient):
        """Package export with an invalid CV must return 422."""
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

        r_pkg = client.post(
            "/api/v1/tailoring/application-package/export",
            headers=_auth(token),
            data={"package_type": "cover_letter_cv"},
        )
        assert r_pkg.status_code == 422, (
            f"Application package with invalid CV must be blocked, "
            f"got {r_pkg.status_code}"
        )

    def test_application_package_blocked_for_skipped_validation(self, client: TestClient):
        """Package export with a skipped-validation CV must return 422."""
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

    def test_application_package_allowed_for_valid_cv(self, client: TestClient):
        """Package export with a valid CV must succeed."""
        token, _ = self._setup_valid_cv(client)

        r_pkg = client.post(
            "/api/v1/tailoring/application-package/export",
            headers=_auth(token),
            data={"package_type": "cover_letter_cv",
                  "cover_letter": "I am applying for this role."},
        )
        assert r_pkg.status_code == 200, (
            f"Valid CV package export must succeed, got {r_pkg.status_code}: {r_pkg.text}"
        )

    def test_cover_letter_only_package_not_blocked_by_validation(self, client: TestClient):
        """A cover-letter-only package must not be blocked even if no CV exists."""
        token = _register_and_login(client)
        r_pkg = client.post(
            "/api/v1/tailoring/application-package/export",
            headers=_auth(token),
            data={"package_type": "cover_letter_only",
                  "cover_letter": "I am applying for this role."},
        )
        assert r_pkg.status_code == 200


class TestEmptySourceExportBlocked:
    """Empty original CV text must produce skipped=True and block export."""

    def test_empty_source_cv_cannot_export(self, client: TestClient):
        """A CV whose validation ran against empty source text must not export.

        Setup: generate a CV normally, then delete the original CV document
        so that _resolve_original_cv_text returns "".  Re-edit the CV — the
        new version's validation will run against empty source text and must
        produce skipped=True.  Export must return 422.
        """
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
            client.post(GENERATE_URL, headers=_auth(token), json={})

        # Delete the original CV document (soft-delete)
        r_cv = client.get("/api/v1/tailoring/cv/latest", headers=_auth(token))
        assert r_cv.status_code == 200
        cv_doc_id = r_cv.json()["id"]
        r_del = client.delete(
            f"/api/v1/tailoring/documents/{cv_doc_id}",
            headers=_auth(token),
        )
        assert r_del.status_code == 204

        # Re-edit the CV.  _resolve_original_cv_text will fail to find the
        # analysis's cv_document (soft-deleted) and return "".
        valid_edit = dict(MOCK_CV_WITH_ALL_SECTIONS)
        valid_edit["edit_summary"] = "Minor tweak after CV deletion."
        valid_edit["needs_clarification"] = False
        valid_edit["section_explanations"] = []

        with patch("backend.ai.cv_editor.CVEditorService.apply_edit",
                   new=AsyncMock(return_value=dict(valid_edit))):
            r_edit = client.post(
                "/api/v1/tailoring/cv/edit",
                headers=_auth(token),
                json={"message": "Improve summary."},
            )
        assert r_edit.status_code == 201
        validation = r_edit.json()["content"]["validation"]
        assert validation["skipped"] is True, (
            "After deleting the source CV, validation must be skipped=True "
            f"(not is_valid=True, skipped=False). Got: {validation}"
        )

        # Export the new version — must be blocked.
        new_cv_id = r_edit.json()["id"]
        for endpoint in ("docx", "pdf"):
            r_exp = client.get(
                f"/api/v1/tailoring/cv/{new_cv_id}/export/{endpoint}",
                headers=_auth(token),
            )
            assert r_exp.status_code == 422, (
                f"Export of skipped-validation CV (empty source) must return 422 "
                f"on {endpoint}, got {r_exp.status_code}"
            )
