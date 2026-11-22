"""
Full end-to-end verification of the CV Tailoring Studio workflow.

Steps verified:
  1. Upload a CV (DOCX).
  2. Save a job description (paste).
  3. Analyse the CV against the JD (mocked LLM).
  4. Generate the tailored CV (mocked LLM).
  5. Verify employment history, education and certificates are preserved.
  6. Verify unsupported claims and placeholders are rejected.
  7. Edit a CV section and save as a new version.
  8. Export DOCX and verify content.
  9. Export PDF and verify content.

LLM calls are mocked — no live API key is required.
No production data is used.
"""

from __future__ import annotations

import io
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.services.cv_validator import CVValidator

# ── Constants ─────────────────────────────────────────────────────────────────

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
CV_UPLOAD_URL = "/api/v1/tailoring/cv/upload"
JD_PASTE_URL = "/api/v1/tailoring/job-description/paste"
ANALYZE_URL = "/api/v1/tailoring/analyze"
GENERATE_URL = "/api/v1/tailoring/cv/generate"

PATCH_JD = "backend.ai.jd_analyzer.JDAnalyzerService.analyze"
PATCH_MATCH = "backend.ai.cv_jd_matcher.CVJDMatcherService.match"
PATCH_RECS = "backend.ai.tailoring_advisor.TailoringAdvisorService.advise"
PATCH_REWRITE = "backend.ai.cv_rewriter.CVRewriterService.rewrite"

# ── Test fixtures ─────────────────────────────────────────────────────────────

REALISTIC_CV = """
MWANSA MATIMBA
Human Resources Manager
Chilenje South, Lusaka, Zambia | +260 977 190 580 | mwansamatimba@gmail.com

PROFESSIONAL EXPERIENCE

Auditing Intern - Public Debt Investment, Jan 2025 - Dec 2025
Office of the Auditor General of Zambia, Lusaka
- Handled official records with confidentiality and accuracy.
- Organised working papers for senior officers.
- Analysed and reconciled information against source documents.

Managing Partner - Business and Digital Solutions Consultant, Jun 2019 - Present
React Digital Consulting, Lusaka
- Run client consultations and deliver career counselling and CV writing.
- Develop business plans and financial models for SMEs.
- Facilitate professional workshops for clients.
- Use Excel, Power BI, Canva and AI tools to prepare accurate reports.

Administrator, Jan 2022 - Apr 2022
Mooh Pooh Car Hire & Tours, Lusaka
- Maintained business records and contract documents.
- Prepared daily, monthly and yearly management reports.

HR Manager / Risk & Data Analyst, Jul 2020 - Jan 2021
eHemp House (eHemp Zambia Limited)
- Supported HR administration and staff coordination.
- Identified and documented organisational risks.
- Collected and analysed operational data.

Associate / Consultant (Human Resources), 2015 - 2017
Laura & Partners Consulting Limited, Lusaka
- Managed employment contracts and HRIS records.
- Led recruitment: sourced, screened and selected candidates.
- Facilitated orientation and training programmes.

Social Media Management Training Consultant, Mar 2021 - Aug 2021
Yojimbo Management Solutions, Lusaka
- Prepared and delivered training materials.
- Coordinated training activities and tracked participant engagement.

Mentor, 2021 - 2022
Google Africa Developer Training Program
- Held regular check-ins and gave feedback to learners.

EDUCATION
Bachelor of Entrepreneurship - Mulungushi University, Zambia (2008 - 2013)
General Certificate of Education - David Livingstone High School (2002 - 2006)

RELEVANT TRAINING AND CERTIFICATES
- Human Resources Management, Udemy (2021)
- Business Management, Tony Elumelu Foundation (2022)
- Become a Business Analyst Path, LinkedIn Learning (2022)
- Strategic Planning Foundations, LinkedIn Learning (2021)
- Zoho CRM, LinkedIn Learning (2021)
- SPSS and Excel Data Analysis, UZSE A (2020)
- Project Planning and Management, UZSE A (2020)
- ZICTA ICT Incubation Programme (2017)

SKILLS
Microsoft Word (Advanced), Excel (Advanced), PowerPoint (Advanced),
Outlook, Teams, Power BI, SPSS

LANGUAGES
English (Fluent), Bemba (Proficient), Nyanja (Proficient),
Tonga (Competent), Lozi (Adequate), French (Conversational)
"""

JD_TEXT = """
Corporate Business Development Executive
Talent House Ltd, Lusaka, Zambia

We are looking for a Corporate Business Development Executive to join our team.

Responsibilities:
- Identify and develop new business opportunities and partnerships.
- Build and maintain relationships with corporate clients.
- Develop proposals and present solutions to stakeholders.
- Manage the full business development pipeline.

Requirements:
- Bachelor's degree in Business Administration or related field.
- 3+ years experience in business development or sales.
- Excellent communication and presentation skills.
- CRM proficiency.

Nice to have:
- MBA or additional business qualifications.
- Experience in HR consulting or talent management.
"""

MOCK_JD_ANALYSIS = {
    "job_title": "Corporate Business Development Executive",
    "employer": "Talent House Ltd",
    "location": "Lusaka, Zambia",
    "employment_type": "full-time",
    "responsibilities": ["Identify and develop new business opportunities", "Build client relationships"],
    "essential_qualifications": ["Bachelor's degree in Business", "3+ years business development"],
    "desirable_qualifications": ["MBA", "HR consulting experience"],
    "required_experience_years": "3 years",
    "technical_skills": ["CRM", "Business Development"],
    "soft_skills": ["Communication", "Presentation"],
    "industry_keywords": ["business development", "client management"],
    "application_requirements": ["CV"],
    "summary": "Corporate business development role at Talent House Ltd.",
}

MOCK_MATCH = {
    "matching_skills": [
        {"skill": "Business Development", "evidence": "Managing Partner, React Digital Consulting", "jd_reference": "business development"},
        {"skill": "Client Management", "evidence": "Run client consultations at React Digital", "jd_reference": "client relationships"},
    ],
    "matching_experience": [
        {"requirement": "Business development", "evidence": "Managing Partner at React Digital since 2019", "strength": "strong"},
    ],
    "matching_achievements": [],
    "missing_requirements": [
        {"requirement": "CRM proficiency", "type": "essential", "note": "Zoho CRM in certificates"},
        {"requirement": "MBA", "type": "desirable", "note": "Holds Bachelor of Entrepreneurship"},
    ],
    "missing_keywords": ["CRM", "pipeline"],
    "weak_sections": [{"section": "Summary", "issue": "HR-focused, not business development", "evidence": "HR and administration professional"}],
    "overall_match_summary": "Strong business development foundation from React Digital. HR background adds consulting context.",
}

MOCK_RECS = {
    "section_recommendations": [
        {"section": "Summary", "action": "rewrite", "reason": "Reposition toward business development",
         "evidence": "HR and administration professional", "jd_reference": "Corporate Business Development Executive",
         "suggestion": "Lead with React Digital business development experience"},
    ],
    "achievements_to_highlight": [],
    "information_to_request": [
        {"question": "Do you have CRM experience?", "reason": "JD requires CRM", "section": "Skills"},
    ],
    "keyword_optimisation": [
        {"keyword": "business development", "action": "integrate", "note": "In experience, integrate into summary"},
    ],
    "priority_actions": ["Rewrite summary", "Prioritise React Digital in experience"],
    "tailoring_summary": "Strong candidate. Reposition away from HR framing toward business development.",
}

MOCK_TAILORED_CV = {
    "sections": [
        {"heading": "MWANSA MATIMBA", "type": "contact",
         "content": "Mwansa Matimba\nChilenje South, Lusaka, Zambia\n+260 977 190 580 | mwansamatimba@gmail.com",
         "items": [], "source": "original"},
        {"heading": "Professional Summary", "type": "summary",
         "content": (
             "Business development professional with six years of experience building client relationships, "
             "developing financial models and delivering consulting services for SMEs. Managing Partner of "
             "React Digital Consulting (2019 to present), where I develop business plans, facilitate workshops "
             "and manage multiple client relationships. HR consulting background at Laura & Partners provides "
             "stakeholder management and proposal-writing experience."
         ),
         "items": [], "source": "rewritten"},
        {"heading": "Professional Experience", "type": "experience", "content": "", "items": [
            {"title": "Managing Partner - Business and Digital Solutions Consultant",
             "company": "React Digital Consulting, Lusaka", "dates": "Jun 2019 - Present",
             "details": ["Develop business plans and financial models for SMEs.",
                         "Run client consultations and deliver targeted solutions.",
                         "Facilitate professional workshops and manage multiple client relationships.",
                         "Use Excel, Power BI, Canva and AI tools to prepare accurate reports."]},
            {"title": "Associate / Consultant (Human Resources)",
             "company": "Laura & Partners Consulting Limited, Lusaka", "dates": "2015 - 2017",
             "details": ["Managed employment contracts and HRIS records.",
                         "Led recruitment: sourced, screened and selected candidates.",
                         "Facilitated orientation and training programmes.",
                         "Prepared HR reports and presentations."]},
            {"title": "HR Manager / Risk & Data Analyst",
             "company": "eHemp House (eHemp Zambia Limited)", "dates": "Jul 2020 - Jan 2021",
             "details": ["Supported HR administration and staff coordination.",
                         "Identified, assessed and documented organisational risks.",
                         "Collected and analysed operational data."]},
            {"title": "Auditing Intern - Public Debt Investment",
             "company": "Office of the Auditor General of Zambia, Lusaka", "dates": "Jan 2025 - Dec 2025",
             "details": ["Handled official and sensitive records with full confidentiality.",
                         "Analysed, reconciled and verified information against source documents."]},
            {"title": "Administrator",
             "company": "Mooh Pooh Car Hire & Tours, Lusaka", "dates": "Jan 2022 - Apr 2022",
             "details": ["Maintained business records and contract documents.",
                         "Prepared daily, monthly and yearly management reports."]},
            {"title": "Social Media Management Training Consultant",
             "company": "Yojimbo Management Solutions, Lusaka", "dates": "Mar 2021 - Aug 2021",
             "details": ["Prepared and delivered training materials.",
                         "Coordinated training activities and tracked participant engagement."]},
            {"title": "Mentor",
             "company": "Google Africa Developer Training Program", "dates": "2021 - 2022",
             "details": ["Held regular check-ins and gave timely feedback to learners.",
                         "Shared learning resources and guided participants."]},
        ], "source": "rewritten"},
        {"heading": "Education", "type": "education", "content": "", "items": [
            {"qualification": "Bachelor of Entrepreneurship", "institution": "Mulungushi University, Zambia", "dates": "2008 - 2013"},
            {"qualification": "General Certificate of Education", "institution": "David Livingstone High School, Zambia", "dates": "2002 - 2006"},
        ], "source": "original"},
        {"heading": "Relevant Training and Certificates", "type": "certifications", "content": "", "items": [
            "Human Resources Management, Udemy (2021)",
            "Business Management, Tony Elumelu Foundation (2022)",
            "Become a Business Analyst Path, LinkedIn Learning (2022)",
            "Strategic Planning Foundations, LinkedIn Learning (2021)",
            "Zoho CRM, LinkedIn Learning (2021)",
            "SPSS and Excel Data Analysis, UZSE A (2020)",
            "Project Planning and Management, UZSE A (2020)",
            "ZICTA ICT Incubation Programme (2017)",
        ], "source": "original"},
        {"heading": "Skills", "type": "skills", "content": "", "items": [
            "Business Development", "Client Relationship Management",
            "Microsoft Word (Advanced)", "Excel (Advanced)", "PowerPoint (Advanced)",
            "Outlook", "Teams", "Power BI", "SPSS", "Zoho CRM",
        ], "source": "rewritten"},
        {"heading": "Languages", "type": "other",
         "content": "English (Fluent)  |  Bemba (Proficient)  |  Nyanja (Proficient)  |  Tonga (Competent)  |  Lozi (Adequate)  |  French (Conversational)",
         "items": [], "source": "original"},
    ],
    "generation_notes": (
        "Rewrote summary and reorganised experience to lead with business development. "
        "Preserved all 7 employment entries, 2 education entries, 8 certificates, all skills and languages."
    ),
}

EDITED_SUMMARY = (
    "Results-driven business development professional with six years of experience growing client "
    "portfolios and developing financial models for SMEs. Managing Partner of React Digital "
    "Consulting (2019 to present). HR consulting background from Laura & Partners strengthens "
    "stakeholder and proposal capabilities."
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _email() -> str:
    return f"e2e_{uuid.uuid4().hex[:8]}@test.jobyn.ai"

def _register_and_login(client: TestClient) -> tuple[str, str]:
    email = _email()
    pw = "E2eVerify!99"
    r = client.post(REGISTER_URL, json={"email": email, "password": pw, "full_name": "E2E Verifier"})
    assert r.status_code == 201, f"Register: {r.text}"
    r = client.post(LOGIN_URL, json={"email": email, "password": pw})
    assert r.status_code == 200, f"Login: {r.text}"
    return r.json()["access_token"], email

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

# ── Test class ─────────────────────────────────────────────────────────────────

class TestFullWorkflow:
    """Full 9-step end-to-end workflow verification."""

    @pytest.fixture(autouse=True)
    def setup(self, client: TestClient) -> None:
        self.client = client
        self.token, self.email = _register_and_login(client)
        self.cv_id: str = ""
        self.jd_id: str = ""
        self.analysis_id: str = ""
        self.tailored_cv_id: str = ""
        self.v2_cv_id: str = ""

    # ── Step 1 ────────────────────────────────────────────────────────────────

    def test_step1_upload_cv(self) -> None:
        """Step 1: Upload a CV and verify extraction preserves all key content."""
        docx = _make_docx(REALISTIC_CV)
        r = self.client.post(CV_UPLOAD_URL, headers=_auth(self.token),
                             files={"file": ("mwansa_cv.docx", docx, DOCX_CT)})
        assert r.status_code == 201, f"Upload failed: {r.text}"
        data = r.json()
        assert data["extraction_status"] == "succeeded", f"extraction_status={data['extraction_status']}"
        self.cv_id = data["id"]
        extracted = data.get("extracted_text", "") or ""
        for expected in ["React Digital", "eHemp House", "Mulungushi", "Laura", "Udemy"]:
            assert expected in extracted, f"Extraction missing: {expected!r}. Got: {extracted[:300]}"

    # ── Step 2 ────────────────────────────────────────────────────────────────

    def test_step2_save_job_description(self) -> None:
        """Step 2: Save JD and verify it is persisted with the correct content."""
        r = self.client.post(JD_PASTE_URL, headers=_auth(self.token), json={"text": JD_TEXT})
        assert r.status_code == 201, f"JD paste failed: {r.text}"
        data = r.json()
        assert data["extraction_status"] == "succeeded"
        self.jd_id = data["id"]
        pasted = data.get("pasted_text") or ""
        assert "Corporate Business Development Executive" in pasted
        assert "Talent House Ltd" in pasted

    # ── Step 3 ────────────────────────────────────────────────────────────────

    def test_step3_analyse_cv_against_jd(self) -> None:
        """Step 3: Run analysis and verify correct document IDs and JD title."""
        # Precondition: CV and JD must exist
        docx = _make_docx(REALISTIC_CV)
        r = self.client.post(CV_UPLOAD_URL, headers=_auth(self.token),
                             files={"file": ("cv.docx", docx, DOCX_CT)})
        assert r.status_code == 201
        cv_id = r.json()["id"]

        r = self.client.post(JD_PASTE_URL, headers=_auth(self.token), json={"text": JD_TEXT})
        assert r.status_code == 201
        jd_id = r.json()["id"]

        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            r = self.client.post(ANALYZE_URL, headers=_auth(self.token), json={})
        assert r.status_code == 201, f"Analyse failed: {r.text}"
        data = r.json()
        self.analysis_id = data["id"]

        assert data.get("cv_document_id") == cv_id, "cv_document_id mismatch"
        assert data.get("jd_document_id") == jd_id, "jd_document_id mismatch"
        assert data.get("error") is None, f"Analysis error: {data.get('error')}"
        assert data.get("recommendations") is not None, "No recommendations"
        assert data["jd_analysis"]["job_title"] == "Corporate Business Development Executive"
        assert len(data["match_result"]["matching_skills"]) >= 2
        assert len(data["match_result"]["missing_requirements"]) >= 1

    # ── Step 4 ────────────────────────────────────────────────────────────────

    def test_step4_generate_tailored_cv(self) -> None:
        """Step 4: Generate tailored CV from latest analysis."""
        docx = _make_docx(REALISTIC_CV)
        self.client.post(CV_UPLOAD_URL, headers=_auth(self.token),
                         files={"file": ("cv.docx", docx, DOCX_CT)})
        self.client.post(JD_PASTE_URL, headers=_auth(self.token), json={"text": JD_TEXT})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            self.client.post(ANALYZE_URL, headers=_auth(self.token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_TAILORED_CV)):
            r = self.client.post(GENERATE_URL, headers=_auth(self.token), json={})
        assert r.status_code == 201, f"Generate failed: {r.text}"
        data = r.json()
        self.tailored_cv_id = data["id"]
        assert data["version_number"] == 1
        assert data["edit_type"] == "initial"
        assert data["status"] == "pending"
        sections = data["content"]["sections"]
        types = [s["type"] for s in sections]
        for expected_type in ["contact", "summary", "experience", "education", "certifications", "skills"]:
            assert expected_type in types, f"Section type '{expected_type}' missing from generated CV"

    # ── Step 5 ────────────────────────────────────────────────────────────────

    def test_step5_content_preservation(self) -> None:
        """Step 5: All 7 employers, 2 education entries, 8 certs, languages, contact preserved."""
        docx = _make_docx(REALISTIC_CV)
        self.client.post(CV_UPLOAD_URL, headers=_auth(self.token),
                         files={"file": ("cv.docx", docx, DOCX_CT)})
        self.client.post(JD_PASTE_URL, headers=_auth(self.token), json={"text": JD_TEXT})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            self.client.post(ANALYZE_URL, headers=_auth(self.token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_TAILORED_CV)):
            r = self.client.post(GENERATE_URL, headers=_auth(self.token), json={})
        assert r.status_code == 201
        cv_id = r.json()["id"]

        r = self.client.get(f"/api/v1/tailoring/cv/{cv_id}", headers=_auth(self.token))
        sections = r.json()["content"]["sections"]

        # 5a — All 7 employers
        exp = [s for s in sections if s["type"] == "experience"][0]
        exp_text = json.dumps(exp["items"])
        for employer in ["React Digital", "Laura & Partners", "eHemp House",
                         "Auditor General", "Mooh Pooh", "Yojimbo", "Google Africa"]:
            assert employer in exp_text, f"Employer missing: {employer!r}"
        assert len(exp["items"]) == 7, f"Expected 7 experience entries, got {len(exp['items'])}"

        # 5b — Education (2 entries)
        edu = [s for s in sections if s["type"] == "education"][0]
        edu_text = json.dumps(edu["items"])
        assert "Mulungushi University" in edu_text
        assert "David Livingstone" in edu_text

        # 5c — Certifications (8 entries)
        certs = [s for s in sections if s["type"] == "certifications"][0]
        cert_text = json.dumps(certs["items"])
        for cert in ["Udemy", "Tony Elumelu", "LinkedIn Learning", "Zoho CRM", "ZICTA"]:
            assert cert in cert_text, f"Certificate missing: {cert!r}"
        assert len(certs["items"]) == 8, f"Expected 8 cert entries, got {len(certs['items'])}"

        # 5d — Languages
        other = [s for s in sections if "English" in (s.get("content") or "")]
        assert other, "Languages section not found"
        for lang in ["English", "Bemba", "Nyanja", "French"]:
            assert lang in other[0]["content"], f"Language missing: {lang}"

        # 5e — Contact: original fields preserved
        contact = [s for s in sections if s["type"] == "contact"][0]
        assert "Mwansa Matimba" in contact["content"]
        assert "+260 977 190 580" in contact["content"]
        assert contact["source"] == "original"

    # ── Step 6 ────────────────────────────────────────────────────────────────

    def test_step6a_reject_fabricated_employer(self) -> None:
        """Step 6a: Fabricated employer not in original CV is rejected."""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Paid Search Manager", "company": "Google Healthcare",
                        "dates": "2019 - Present", "details": ["Managed PPC campaigns"]}]},
        ]
        result = CVValidator.validate(sections, REALISTIC_CV)
        assert not result.is_valid, "Validator should reject fabricated employer 'Google Healthcare'"
        claims = [c["claim"] for c in result.unsupported_claims]
        assert any("Google Healthcare" in c for c in claims), f"Wrong claim flagged: {claims}"

    def test_step6b_reject_placeholder_title(self) -> None:
        """Step 6b: Placeholder job title 'Job title' is rejected."""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Job title", "company": "React Digital Consulting",
                        "dates": "2019 - Present", "details": ["Did things"]}]},
        ]
        result = CVValidator.validate(sections, REALISTIC_CV)
        assert not result.is_valid, "Validator should reject placeholder title 'Job title'"

    def test_step6c_reject_out_of_range_date(self) -> None:
        """Step 6c: Dates outside the original CV's year range are rejected."""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "HR Manager", "company": "eHemp House",
                        "dates": "2026 - 2028", "details": ["Did HR things"]}]},
        ]
        result = CVValidator.validate(sections, REALISTIC_CV)
        assert not result.is_valid, "Years 2026-2028 not in original CV — should be rejected"

    def test_step6d_valid_original_content_accepted(self) -> None:
        """Step 6d: Valid original employer and dates are not incorrectly rejected."""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Managing Partner", "company": "React Digital Consulting",
                        "dates": "Jun 2019 - Present",
                        "details": ["Develop business plans for SMEs"]}]},
        ]
        result = CVValidator.validate(sections, REALISTIC_CV)
        assert result.is_valid, (
            f"Valid content incorrectly rejected: {result.unsupported_claims}"
        )

    def test_step6e_generated_cv_passes_validation(self) -> None:
        """Step 6e: Validation result embedded in the generated CV must pass."""
        docx = _make_docx(REALISTIC_CV)
        self.client.post(CV_UPLOAD_URL, headers=_auth(self.token),
                         files={"file": ("cv.docx", docx, DOCX_CT)})
        self.client.post(JD_PASTE_URL, headers=_auth(self.token), json={"text": JD_TEXT})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            self.client.post(ANALYZE_URL, headers=_auth(self.token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_TAILORED_CV)):
            r = self.client.post(GENERATE_URL, headers=_auth(self.token), json={})
        cv_id = r.json()["id"]

        r = self.client.get(f"/api/v1/tailoring/cv/{cv_id}", headers=_auth(self.token))
        validation = r.json()["content"].get("validation", {})
        assert validation, "No validation data in generated CV content"
        assert validation["is_valid"] is True, (
            f"Generated CV has unsupported claims: {validation.get('unsupported_claims', [])}"
        )

    # ── Step 7 ────────────────────────────────────────────────────────────────

    def test_step7_edit_section_and_save(self) -> None:
        """Step 7: Edit summary, save as v2; confirm v1 is immutable and v2 persists."""
        docx = _make_docx(REALISTIC_CV)
        self.client.post(CV_UPLOAD_URL, headers=_auth(self.token),
                         files={"file": ("cv.docx", docx, DOCX_CT)})
        self.client.post(JD_PASTE_URL, headers=_auth(self.token), json={"text": JD_TEXT})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            self.client.post(ANALYZE_URL, headers=_auth(self.token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_TAILORED_CV)):
            r = self.client.post(GENERATE_URL, headers=_auth(self.token), json={})
        v1_id = r.json()["id"]
        v1_sections = r.json()["content"]["sections"]
        v1_summary = next(s["content"] for s in v1_sections if s["type"] == "summary")

        # Edit the summary
        edited = []
        for s in v1_sections:
            s2 = dict(s)
            if s2["type"] == "summary":
                s2["content"] = EDITED_SUMMARY
            edited.append(s2)

        r = self.client.post(
            f"/api/v1/tailoring/cv/{v1_id}/visual-edit",
            headers=_auth(self.token),
            json={"sections": edited},
        )
        assert r.status_code == 201, f"visual-edit failed: {r.text}"
        v2 = r.json()
        v2_id = v2["id"]

        assert v2["version_number"] == 2
        assert v2["edit_type"] == "visual_edit"
        v2_summary = next(s["content"] for s in v2["content"]["sections"] if s["type"] == "summary")
        assert EDITED_SUMMARY in v2_summary, f"Edited summary not persisted: {v2_summary[:100]}"

        # v1 must still have the original summary (immutable)
        r = self.client.get(f"/api/v1/tailoring/cv/{v1_id}", headers=_auth(self.token))
        v1_check = next(s["content"] for s in r.json()["content"]["sections"] if s["type"] == "summary")
        assert EDITED_SUMMARY not in v1_check, "v1 was mutated — should be immutable"
        assert v1_summary in v1_check, "v1 summary changed unexpectedly"

    # ── Step 8 ────────────────────────────────────────────────────────────────

    def test_step8_export_docx(self) -> None:
        """Step 8: Export DOCX; verify file format, content-type, and expected CV content."""
        docx = _make_docx(REALISTIC_CV)
        self.client.post(CV_UPLOAD_URL, headers=_auth(self.token),
                         files={"file": ("cv.docx", docx, DOCX_CT)})
        self.client.post(JD_PASTE_URL, headers=_auth(self.token), json={"text": JD_TEXT})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            self.client.post(ANALYZE_URL, headers=_auth(self.token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_TAILORED_CV)):
            r = self.client.post(GENERATE_URL, headers=_auth(self.token), json={})
        cv_id = r.json()["id"]

        r = self.client.get(f"/api/v1/tailoring/cv/{cv_id}/export/docx", headers=_auth(self.token))
        assert r.status_code == 200, f"DOCX export failed: {r.text[:200]}"
        assert "wordprocessingml" in r.headers.get("content-type", ""), f"Wrong content-type: {r.headers.get('content-type')}"
        assert len(r.content) > 1000, f"DOCX too small: {len(r.content)} bytes"

        import zipfile
        assert zipfile.is_zipfile(io.BytesIO(r.content)), "DOCX is not a valid ZIP/Office file"

        from docx import Document
        doc = Document(io.BytesIO(r.content))
        text = "\n".join(p.text for p in doc.paragraphs)
        for expected in [
            "Mwansa Matimba",
            "React Digital Consulting",
            "eHemp House",
            "Laura & Partners",
            "Mulungushi University",
            "Tony Elumelu",
            "Zoho CRM",
        ]:
            assert expected in text, f"DOCX missing: {expected!r}"

    # ── Step 9 ────────────────────────────────────────────────────────────────

    def test_step9_export_pdf(self) -> None:
        """Step 9: Export PDF; verify %PDF- header, selectable text, and CV content."""
        docx = _make_docx(REALISTIC_CV)
        self.client.post(CV_UPLOAD_URL, headers=_auth(self.token),
                         files={"file": ("cv.docx", docx, DOCX_CT)})
        self.client.post(JD_PASTE_URL, headers=_auth(self.token), json={"text": JD_TEXT})
        with (
            patch(PATCH_JD, new=AsyncMock(return_value=MOCK_JD_ANALYSIS)),
            patch(PATCH_MATCH, new=AsyncMock(return_value=MOCK_MATCH)),
            patch(PATCH_RECS, new=AsyncMock(return_value=MOCK_RECS)),
        ):
            self.client.post(ANALYZE_URL, headers=_auth(self.token), json={})
        with patch(PATCH_REWRITE, new=AsyncMock(return_value=MOCK_TAILORED_CV)):
            r = self.client.post(GENERATE_URL, headers=_auth(self.token), json={})
        cv_id = r.json()["id"]

        r = self.client.get(f"/api/v1/tailoring/cv/{cv_id}/export/pdf", headers=_auth(self.token))
        assert r.status_code == 200, f"PDF export failed: {r.text[:200]}"
        assert r.headers.get("content-type") == "application/pdf", f"Wrong content-type: {r.headers.get('content-type')}"
        assert r.content[:5] == b"%PDF-", f"Response does not start with %PDF-"
        assert len(r.content) > 1000, f"PDF too small: {len(r.content)} bytes"

        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(r.content))
        pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert pdf_text.strip(), "PDF has no selectable text layer"
        for expected in [
            "Mwansa Matimba",
            "React Digital",
            "eHemp House",
            "Laura",
            "Mulungushi",
            "Tony Elumelu",
        ]:
            assert expected in pdf_text, f"PDF missing: {expected!r}. Extracted: {pdf_text[:200]}"
