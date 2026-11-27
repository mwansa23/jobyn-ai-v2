"""Regression tests for the two confirmed CV Tailoring Studio production bugs.

Bug 1: Stale tailored CV displayed when documents change.
  - Covered by TestRewriterCompletenessValidation (backend logic)
  - Frontend restoreTailoringSession fix is in demo.html (not unit-testable
    without a browser; verified manually or via E2E tooling)

Bug 2: CV sections truncated due to max_tokens=8192.
  - Covered by TestRewriterCompletenessValidation
"""

from __future__ import annotations

import io
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend.ai.cv_rewriter import CVRewriterService

# ── Shared constants ──────────────────────────────────────────────────────────

REGISTER_URL   = "/api/v1/auth/register"
LOGIN_URL      = "/api/v1/auth/login"
CV_UPLOAD_URL  = "/api/v1/tailoring/cv/upload"
JD_PASTE_URL   = "/api/v1/tailoring/job-description/paste"
ANALYZE_URL    = "/api/v1/tailoring/analyze"
GENERATE_URL   = "/api/v1/tailoring/cv/generate"
TAILORED_LATEST_URL = "/api/v1/tailoring/cv/tailored/latest"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_PATCH_JD      = "backend.ai.jd_analyzer.JDAnalyzerService.analyze"
_PATCH_MATCH   = "backend.ai.cv_jd_matcher.CVJDMatcherService.match"
_PATCH_RECS    = "backend.ai.tailoring_advisor.TailoringAdvisorService.advise"
_PATCH_REWRITE = "backend.ai.cv_rewriter.CVRewriterService.rewrite"

_MOCK_JD = {
    "job_title": "Business Development Executive", "employer": "Talent House",
    "location": "Lusaka", "employment_type": "full-time",
    "responsibilities": ["Lead BD"], "essential_qualifications": ["Degree"],
    "desirable_qualifications": [], "required_experience_years": "3",
    "technical_skills": ["CRM"], "soft_skills": ["Communication"],
    "industry_keywords": ["business development"], "application_requirements": [],
    "summary": "BD role",
}
_MOCK_MATCH = {
    "matching_skills": [], "matching_experience": [], "matching_achievements": [],
    "missing_requirements": [], "missing_keywords": [], "weak_sections": [],
    "overall_match_summary": "Good match.",
}
_MOCK_RECS = {
    "section_recommendations": [], "achievements_to_highlight": [],
    "information_to_request": [], "keyword_optimisation": [],
    "priority_actions": ["Rewrite summary"],
    "tailoring_summary": "Reposition toward BD.",
}

# A realistic CV text that includes Certifications and Languages sections
_LONG_CV_TEXT = """
Mwansa Matimba
Lusaka, Zambia | +260 977 190 580 | mwansamatimba@gmail.com

PROFESSIONAL EXPERIENCE

Managing Partner, React Digital Consulting (Jun 2019 - Present)
Run client consultations and deliver career counselling.
Develop business plans and financial models for SMEs.

HR Manager, eHemp House (Jul 2020 - Jan 2021)
Supported HR administration and staff coordination.

EDUCATION

Bachelor of Entrepreneurship, Mulungushi University (2008 - 2013)

RELEVANT TRAINING AND CERTIFICATES

Human Resources Management, Udemy (2021)
Business Management, Tony Elumelu Foundation (2022)
Become a Business Analyst Path, LinkedIn Learning (2022)

SKILLS

Microsoft Word, Excel, PowerPoint, Power BI, SPSS

LANGUAGES

English (Fluent), Bemba (Proficient), Nyanja (Proficient)
"""

def _make_docx(text: str) -> bytes:
    doc = Document()
    for line in text.strip().split("\n"):
        doc.add_paragraph(line.strip())
    buf = io.BytesIO(); doc.save(buf); return buf.getvalue()

def _register_and_login(client: TestClient) -> str:
    import uuid as _uuid
    email = f"bug_fix_{_uuid.uuid4().hex[:8]}@example.com"
    pw = "BugFix123!"
    client.post(REGISTER_URL, json={"email": email, "password": pw, "full_name": "Bug Test"})
    r = client.post(LOGIN_URL, json={"email": email, "password": pw})
    return r.json()["access_token"]

def _auth(token: str):
    return {"Authorization": f"Bearer {token}"}

def _full_pipeline(client: TestClient, token: str, cv_text: str = _LONG_CV_TEXT) -> str:
    """Upload CV, paste JD, run analysis. Return cv_doc id."""
    docx = _make_docx(cv_text)
    r = client.post(CV_UPLOAD_URL, headers=_auth(token),
                    files={"file": ("cv.docx", docx, DOCX_CT)})
    assert r.status_code == 201, r.text
    cv_id = r.json()["id"]
    client.post(JD_PASTE_URL, headers=_auth(token), json={"text": "Business Development Executive role at Talent House."})
    with (
        patch(_PATCH_JD,    new=AsyncMock(return_value=_MOCK_JD)),
        patch(_PATCH_MATCH, new=AsyncMock(return_value=_MOCK_MATCH)),
        patch(_PATCH_RECS,  new=AsyncMock(return_value=_MOCK_RECS)),
    ):
        r = client.post(ANALYZE_URL, headers=_auth(token), json={})
    assert r.status_code == 201, r.text
    return cv_id


# ── Bug 1: Stale CV not displayed for new documents ───────────────────────────

class TestStaleAnalysisCVNotDisplayed:
    """Bug 1 regression: a tailored CV generated for JD-A must not be presented
    as the current result after the user switches to JD-B."""

    def _build_complete_cv(self, sections=None) -> dict[str, Any]:
        """Return a mock rewriter response with the expected section set."""
        return {
            "sections": sections or [
                {"heading": "Contact", "type": "contact",
                 "content": "Mwansa Matimba\nLusaka", "items": [], "source": "original"},
                {"heading": "Professional Summary", "type": "summary",
                 "content": "Business development professional.", "items": [], "source": "rewritten"},
                {"heading": "Professional Experience", "type": "experience", "content": "", "items": [
                    {"title": "Managing Partner", "company": "React Digital Consulting",
                     "dates": "Jun 2019 - Present", "details": ["Develop business plans"]},
                ], "source": "rewritten"},
                {"heading": "Education", "type": "education", "content": "", "items": [
                    {"qualification": "Bachelor of Entrepreneurship",
                     "institution": "Mulungushi University", "dates": "2008 - 2013"},
                ], "source": "original"},
                {"heading": "Certificates", "type": "certifications", "content": "", "items": [
                    "Human Resources Management, Udemy (2021)",
                ], "source": "original"},
                {"heading": "SKILLS", "type": "skills", "content": "", "items": [
                    "Microsoft Word", "Excel", "PowerPoint",
                ], "source": "original"},
                {"heading": "Languages", "type": "other",
                 "content": "English (Fluent), Bemba (Proficient)", "items": [], "source": "original"},
            ],
            "generation_notes": "Rewrote summary for BD focus.",
        }

    def test_tailored_cv_analysis_id_matches_current_analysis(
        self, client: TestClient
    ) -> None:
        """The returned tailored CV must carry the analysis_id of the most
        recent analysis, not an old one."""
        token = _register_and_login(client)
        _full_pipeline(client, token)

        with patch(_PATCH_REWRITE, new=AsyncMock(return_value=self._build_complete_cv())):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})
        assert r.status_code == 201, r.text
        tailored = r.json()
        analysis_id_in_cv = tailored.get("analysis_id")

        # Retrieve the current analysis
        from backend.api.routes.tailoring import router  # noqa
        r_analysis = client.get("/api/v1/tailoring/analysis/latest", headers=_auth(token))
        assert r_analysis.status_code == 200
        current_analysis_id = r_analysis.json()["id"]

        assert analysis_id_in_cv == current_analysis_id, (
            "Tailored CV must reference the current analysis, not a stale one. "
            f"CV has analysis_id={analysis_id_in_cv}, current={current_analysis_id}"
        )

    def test_changing_jd_invalidates_analysis_before_new_generation(
        self, client: TestClient
    ) -> None:
        """After pasting a new JD, generating without re-running analysis must
        be blocked by the stale-analysis guard.

        Protocol:
        1. Upload CV and paste JD-A. Run analysis (links to JD-A).
        2. Confirm the analysis.jd_document_id == JD-A's document ID.
        3. Paste JD-B (new document ID). Do NOT re-run analysis.
        4. Attempt generate → must return 422 because the analysis still
           points to JD-A while the latest JD document is now JD-B.
        """
        import uuid as _uuid
        token = _register_and_login(client)

        # Step 1: upload CV
        docx = _make_docx(_LONG_CV_TEXT)
        r_cv = client.post(CV_UPLOAD_URL, headers=_auth(token),
                           files={"file": ("cv.docx", docx, DOCX_CT)})
        assert r_cv.status_code == 201
        cv_doc_id = r_cv.json()["id"]

        # Step 2: paste JD-A with a unique sentinel phrase
        jd_a_sentinel = f"SENTINEL-JDA-{_uuid.uuid4().hex}"
        r_jda = client.post(JD_PASTE_URL, headers=_auth(token),
                            json={"text": f"Business Development Executive role. {jd_a_sentinel}"})
        assert r_jda.status_code == 201
        jd_a_id = r_jda.json()["id"]

        # Step 3: run analysis — this links to JD-A
        with (
            patch(_PATCH_JD,    new=AsyncMock(return_value=_MOCK_JD)),
            patch(_PATCH_MATCH, new=AsyncMock(return_value=_MOCK_MATCH)),
            patch(_PATCH_RECS,  new=AsyncMock(return_value=_MOCK_RECS)),
        ):
            r_analysis = client.post(ANALYZE_URL, headers=_auth(token), json={})
        assert r_analysis.status_code == 201
        analysis = r_analysis.json()
        # Confirm the analysis links to the correct documents
        assert analysis["cv_document_id"] == cv_doc_id
        assert analysis["jd_document_id"] == jd_a_id, (
            f"Analysis must link to JD-A ({jd_a_id}), "
            f"got jd_document_id={analysis['jd_document_id']}"
        )

        # Step 4: paste JD-B with a different unique sentinel phrase
        jd_b_sentinel = f"SENTINEL-JDB-{_uuid.uuid4().hex}"
        r_jdb = client.post(JD_PASTE_URL, headers=_auth(token),
                            json={"text": f"Senior Marketing Director at a multinational. {jd_b_sentinel}"})
        assert r_jdb.status_code == 201
        jd_b_id = r_jdb.json()["id"]

        # Confirm the two JDs have distinct IDs (the test premise)
        assert jd_a_id != jd_b_id, "JD-A and JD-B must be distinct documents"

        # Soft-delete JD-A so get_latest_job_description unambiguously returns
        # JD-B, regardless of SQLite created_at timestamp ties.
        r_del = client.delete(f"/api/v1/tailoring/documents/{jd_a_id}",
                              headers=_auth(token))
        assert r_del.status_code == 204

        # Step 5: attempt generate WITHOUT re-running analysis.
        # Visible JD = JD-B; analysis.jd_document_id = JD-A (deleted).
        # Stale-analysis guard must fire: 422.
        r_blocked = client.post(GENERATE_URL, headers=_auth(token), json={})
        assert r_blocked.status_code == 422, (
            "Generating after JD change without re-analysis must return 422. "
            f"Got {r_blocked.status_code}. "
            f"JD-A id={jd_a_id}, JD-B id={jd_b_id}, "
            f"analysis.jd_document_id={analysis['jd_document_id']}"
        )
        detail = r_blocked.json()["detail"].lower()
        assert any(phrase in detail for phrase in
                   ["different job description", "stale", "re-run", "re-run the analysis"]), (
            f"422 detail should explain the stale analysis: {r_blocked.json()['detail']}"
        )

    def test_new_jd_is_used_after_re_analysis(self, client: TestClient) -> None:
        """After pasting JD-B and re-running analysis, the new analysis must
        reference JD-B's document ID and JD-A's sentinel text must NOT appear
        in the analysis's linked JD document.

        Uses sentinel phrases to unambiguously distinguish JD-A from JD-B
        without relying on timestamp ordering.
        """
        import uuid as _uuid
        token = _register_and_login(client)

        # Upload CV
        docx = _make_docx(_LONG_CV_TEXT)
        client.post(CV_UPLOAD_URL, headers=_auth(token),
                    files={"file": ("cv.docx", docx, DOCX_CT)})

        # Paste JD-A with a unique sentinel
        jd_a_sentinel = f"SENTINEL-JDA-{_uuid.uuid4().hex}"
        r_jda = client.post(JD_PASTE_URL, headers=_auth(token),
                            json={"text": f"Business Development Executive role. {jd_a_sentinel}"})
        assert r_jda.status_code == 201
        jd_a_id = r_jda.json()["id"]

        # Run analysis against JD-A
        with (
            patch(_PATCH_JD,    new=AsyncMock(return_value=_MOCK_JD)),
            patch(_PATCH_MATCH, new=AsyncMock(return_value=_MOCK_MATCH)),
            patch(_PATCH_RECS,  new=AsyncMock(return_value=_MOCK_RECS)),
        ):
            client.post(ANALYZE_URL, headers=_auth(token), json={})

        # Paste JD-B with a unique sentinel
        jd_b_sentinel = f"SENTINEL-JDB-{_uuid.uuid4().hex}"
        r_jdb = client.post(JD_PASTE_URL, headers=_auth(token),
                            json={"text": f"Senior Marketing Director at a multinational firm. {jd_b_sentinel}"})
        assert r_jdb.status_code == 201
        jd_b_id = r_jdb.json()["id"]
        assert jd_a_id != jd_b_id, "JD-A and JD-B must be distinct documents"

        # Soft-delete JD-A so get_latest_job_description unambiguously returns
        # JD-B when re-analysis runs, regardless of SQLite timestamp ties.
        r_del = client.delete(f"/api/v1/tailoring/documents/{jd_a_id}",
                              headers=_auth(token))
        assert r_del.status_code == 204

        # Re-run analysis — the backend uses get_latest_job_description()
        # which now unambiguously returns JD-B.
        with (
            patch(_PATCH_JD,    new=AsyncMock(return_value=_MOCK_JD)),
            patch(_PATCH_MATCH, new=AsyncMock(return_value=_MOCK_MATCH)),
            patch(_PATCH_RECS,  new=AsyncMock(return_value=_MOCK_RECS)),
        ):
            r_analysis = client.post(ANALYZE_URL, headers=_auth(token), json={})
        assert r_analysis.status_code == 201
        analysis = r_analysis.json()

        # 1. Analysis must reference JD-B's document ID, not JD-A's.
        assert analysis["jd_document_id"] == jd_b_id, (
            "Analysis after JD-B paste must reference JD-B's document ID. "
            f"Got jd_document_id={analysis['jd_document_id']}, "
            f"JD-A id={jd_a_id}, JD-B id={jd_b_id}."
        )

        # 2. The JD document linked by the analysis must contain JD-B's sentinel
        #    and must NOT contain JD-A's sentinel.
        r_jd_doc = client.get(f"/api/v1/tailoring/documents/{jd_b_id}",
                               headers=_auth(token))
        assert r_jd_doc.status_code == 200
        jd_b_text = (
            r_jd_doc.json().get("extracted_text") or
            r_jd_doc.json().get("pasted_text") or ""
        )
        assert jd_b_sentinel in jd_b_text, (
            f"JD-B document must contain its sentinel. Got: {jd_b_text[:200]}"
        )
        assert jd_a_sentinel not in jd_b_text, (
            "JD-B document must NOT contain JD-A's sentinel — JD-A must not contaminate JD-B."
        )


# ── Bug 2: Section completeness validation ────────────────────────────────────

class TestRewriterCompletenessValidation:
    """Bug 2 regression: completeness validation and generation_notes honesty."""

    def _make_complete_response(self, include_certs: bool = True) -> dict[str, Any]:
        sections = [
            {"heading": "Contact", "type": "contact",
             "content": "Test User", "items": [], "source": "original"},
            {"heading": "Professional Summary", "type": "summary",
             "content": "Summary here.", "items": [], "source": "rewritten"},
            {"heading": "Professional Experience", "type": "experience", "content": "", "items": [
                {"title": "Managing Partner", "company": "React Digital",
                 "dates": "2019-Present", "details": ["Ran the business"]},
            ], "source": "rewritten"},
            {"heading": "Education", "type": "education", "content": "", "items": [
                {"qualification": "Bachelor of Entrepreneurship",
                 "institution": "Mulungushi University", "dates": "2008-2013"},
            ], "source": "original"},
        ]
        if include_certs:
            sections.append({
                "heading": "Relevant Training and Certificates", "type": "certifications",
                "content": "", "items": ["Human Resources Management, Udemy (2021)"],
                "source": "original",
            })
            sections.append({
                "heading": "SKILLS", "type": "skills", "content": "",
                "items": ["Microsoft Word", "Excel", "PowerPoint"],
                "source": "original",
            })
            sections.append({
                "heading": "Languages", "type": "other",
                "content": "English (Fluent), Bemba (Proficient)", "items": [],
                "source": "original",
            })
        return {"sections": sections, "generation_notes": "Rewrote summary."}

    def test_complete_response_has_no_missing_section_warning(self) -> None:
        """When the LLM returns all source sections, generation_notes must NOT
        contain a WARNING about missing sections."""
        svc = CVRewriterService()
        result = deepcopy(self._make_complete_response(include_certs=True))
        svc._validate_and_correct_notes(result, _LONG_CV_TEXT)
        notes = result["generation_notes"]
        assert "WARNING" not in notes, (
            f"Complete output should not trigger a WARNING in generation_notes, got: {notes!r}"
        )
        assert "_missing_sections" not in result

    def test_truncated_response_flags_missing_sections(self) -> None:
        """When the LLM omits sections (simulating token truncation), the
        completeness validator must set a WARNING in generation_notes and
        populate _missing_sections."""
        svc = CVRewriterService()
        # Return a response WITHOUT certifications and languages
        result = deepcopy(self._make_complete_response(include_certs=False))
        svc._validate_and_correct_notes(result, _LONG_CV_TEXT)
        notes = result["generation_notes"]
        assert "WARNING" in notes, (
            f"Truncated output (missing certs/languages) must WARNING in generation_notes, got: {notes!r}"
        )
        missing = result.get("_missing_sections", [])
        assert len(missing) > 0, "Missing sections list must be non-empty when sections are absent"

    def test_generation_notes_cannot_falsely_claim_completeness(self) -> None:
        """If the LLM claims to have preserved certificates but the section is
        absent from the output, the validator must overwrite those notes with
        an honest WARNING."""
        svc = CVRewriterService()
        result = deepcopy(self._make_complete_response(include_certs=False))
        # Inject a dishonest LLM self-report
        result["generation_notes"] = (
            "Preserved all 8 certificates and languages from original CV."
        )
        svc._validate_and_correct_notes(result, _LONG_CV_TEXT)
        notes = result["generation_notes"]
        assert "WARNING" in notes, (
            "Validator must overwrite dishonest LLM notes when sections are missing. "
            f"Got: {notes!r}"
        )

    def test_detect_source_sections_finds_headings(self) -> None:
        """_detect_source_sections must identify the key headings in the CV."""
        headings = CVRewriterService._detect_source_sections(_LONG_CV_TEXT)
        headings_lower = [h.lower() for h in headings]
        assert any("experience" in h for h in headings_lower), "Should find PROFESSIONAL EXPERIENCE"
        assert any("education" in h for h in headings_lower), "Should find EDUCATION"
        assert any("certificate" in h or "training" in h for h in headings_lower), (
            "Should find RELEVANT TRAINING AND CERTIFICATES"
        )
        assert any("language" in h for h in headings_lower), "Should find LANGUAGES"

    def test_long_cv_generation_uses_16384_max_tokens(self, client: TestClient) -> None:
        """The generate endpoint must call generate_json with max_tokens=16384,
        not the old 8192 value."""
        token = _register_and_login(client)
        _full_pipeline(client, token, cv_text=_LONG_CV_TEXT)

        captured_kwargs: list[dict] = []

        async def _capture_generate_json(prompt, **kwargs):
            captured_kwargs.append(kwargs)
            # Return a minimal valid CV to avoid LLM call
            return self._make_complete_response(include_certs=True)

        with patch("backend.ai.cv_rewriter.generate_json", side_effect=_capture_generate_json):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})

        assert r.status_code == 201, r.text
        assert len(captured_kwargs) >= 1
        actual_max_tokens = captured_kwargs[0].get("max_tokens")
        assert actual_max_tokens == 16384, (
            f"generate_json must be called with max_tokens=16384 (not {actual_max_tokens})"
        )

    def test_certifications_section_present_in_generation_output(
        self, client: TestClient
    ) -> None:
        """When the rewriter returns a complete response (mocked), the
        certifications and languages sections must survive to the API response."""
        token = _register_and_login(client)
        _full_pipeline(client, token, cv_text=_LONG_CV_TEXT)

        complete_cv = self._make_complete_response(include_certs=True)
        with patch(_PATCH_REWRITE, new=AsyncMock(return_value=complete_cv)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})

        assert r.status_code == 201, r.text
        sections = r.json()["content"]["sections"]
        types = {s["type"] for s in sections}
        assert "certifications" in types, (
            f"Generated CV must include certifications section. Types present: {types}"
        )
        other_sections = [s for s in sections if s["type"] == "other"]
        languages_found = any(
            "english" in (s.get("content") or "").lower() or
            "english" in str(s.get("items", [])).lower()
            for s in other_sections
        )
        assert languages_found, (
            "Generated CV must include the Languages section in the output"
        )

    def test_generation_notes_missing_warning_exposed_in_api(
        self, client: TestClient
    ) -> None:
        """When the validator detects missing sections, the WARNING must appear
        in generation_notes in the API response so the UI can surface it."""
        token = _register_and_login(client)
        _full_pipeline(client, token, cv_text=_LONG_CV_TEXT)

        truncated_cv = self._make_complete_response(include_certs=False)
        truncated_cv["generation_notes"] = "Preserved all certificates."  # dishonest
        with patch(_PATCH_REWRITE, new=AsyncMock(return_value=truncated_cv)):
            r = client.post(GENERATE_URL, headers=_auth(token), json={})

        assert r.status_code == 201, r.text
        notes = r.json()["content"].get("generation_notes", "")
        assert "WARNING" in notes, (
            f"API response generation_notes must contain WARNING when sections are "
            f"missing. Got: {notes!r}"
        )
