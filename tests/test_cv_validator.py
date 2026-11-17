"""Tests for the deterministic factual CV validation service.

No LLM calls are made — all tests use synthetic structured CV sections and
original CV text to verify deterministic validation logic.
"""

from __future__ import annotations

import pytest

from backend.services.cv_validator import CVValidator, CVValidationResult


# ── Test CVs ─────────────────────────────────────────────────────────────────

ORIGINAL_CV = """
Jane Smith
Senior Software Engineer
jane@example.com | +260 97 1234567

Experience:
- Senior Developer at TechCorp (2020-2024): Built Python REST APIs with FastAPI.
  Led microservices migration. Reduced API latency by 40%.
- Developer at DataStart (2018-2020): Built Python data pipelines.
  Used PostgreSQL and Docker.

Skills: Python, FastAPI, PostgreSQL, Docker, Git, Linux
Education: BSc Computer Science, University of Zambia (2014-2018)
Certifications: AWS Certified Solutions Architect
"""

# ── Test cases ────────────────────────────────────────────────────────────────


class TestSupportedClaims:
    """Claims that are directly traceable to the original CV text."""

    def test_supported_employer_passes(self):
        sections = [{"heading": "Experience", "type": "experience", "items": [
            {"title": "Senior Developer", "company": "TechCorp", "dates": "2020-2024",
             "details": ["Built Python REST APIs with FastAPI"]},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert result.is_valid
        assert len(result.unsupported_claims) == 0

    def test_supported_skill_passes(self):
        sections = [{"heading": "Skills", "type": "skills", "items": [
            "Python", "FastAPI", "PostgreSQL", "Docker",
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert result.is_valid

    def test_supported_education_passes(self):
        sections = [{"heading": "Education", "type": "education", "items": [
            {"institution": "University of Zambia", "qualification": "BSc Computer Science",
             "dates": "2014-2018"},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert result.is_valid

    def test_supported_certification_passes(self):
        sections = [{"heading": "Certifications", "type": "certifications", "items": [
            "AWS Certified Solutions Architect",
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert result.is_valid


class TestUnsupportedClaims:
    """Claims that have no basis in the original CV text."""

    def test_fabricated_employer_fails(self):
        sections = [{"heading": "Experience", "type": "experience", "items": [
            {"title": "Senior Developer", "company": "Google", "dates": "2020-2024",
             "details": ["Built Python REST APIs"]},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert not result.is_valid
        assert len(result.unsupported_claims) == 1
        assert "Google" in result.unsupported_claims[0]["claim"]

    def test_fabricated_date_fails(self):
        sections = [{"heading": "Experience", "type": "experience", "items": [
            {"title": "Developer", "company": "DataStart", "dates": "2025-2027",
             "details": ["Built Python data pipelines"]},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert not result.is_valid
        claim_text = " ".join(c["detail"] for c in result.unsupported_claims)
        assert "2025" in claim_text or "2027" in claim_text

    def test_fabricated_institution_fails(self):
        sections = [{"heading": "Education", "type": "education", "items": [
            {"institution": "Harvard University", "qualification": "MBA",
             "dates": "2014-2018"},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert not result.is_valid

    def test_fabricated_skill_flagged(self):
        sections = [{"heading": "Skills", "type": "skills", "items": [
            "Python", "Kubernetes",
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        # Kubernetes is not in the original — should be needs_confirmation at minimum
        all_findings = result.findings
        k8s_finding = [f for f in all_findings if "Kubernetes" in f.get("claim", "")]
        assert len(k8s_finding) == 1
        assert k8s_finding[0]["classification"] in ("needs_confirmation", "unsupported")


class TestReasonableRewording:
    """Claims that are supported but reworded."""

    def test_reworded_title_accepted(self):
        sections = [{"heading": "Experience", "type": "experience", "items": [
            {"title": "Lead Software Engineer", "company": "TechCorp", "dates": "2020-2024",
             "details": ["Architected and delivered Python REST APIs using FastAPI"]},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        # Should pass because employer and dates match; title is a rewording
        assert result.is_valid


class TestQuantifiedFigures:
    """Quantified achievement validation."""

    def test_supported_figure_passes(self):
        sections = [{"heading": "Experience", "type": "experience", "items": [
            {"title": "Senior Developer", "company": "TechCorp", "dates": "2020-2024",
             "details": ["Reduced API latency by 40%"]},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert result.is_valid

    def test_fabricated_figure_flagged(self):
        sections = [{"heading": "Experience", "type": "experience", "items": [
            {"title": "Senior Developer", "company": "TechCorp", "dates": "2020-2024",
             "details": ["Increased revenue by 500%"]},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        # "500%" is not in the original — should be flagged
        all_findings = result.findings + result.warnings + result.unsupported_claims
        fig_finding = [f for f in all_findings if "500" in f.get("claim", "") or "500" in f.get("detail", "")]
        assert len(fig_finding) >= 1
        assert fig_finding[0]["classification"] in ("needs_confirmation", "unsupported")


class TestContactAndOriginalSections:
    """Contact and 'original' sourced sections should always pass."""

    def test_contact_section_always_supported(self):
        sections = [{"heading": "Contact", "type": "contact",
                      "content": "Jane Smith\njane@example.com", "source": "original"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert result.is_valid

    def test_original_source_sections_pass(self):
        sections = [{"heading": "Education", "type": "education", "items": [
            "BSc Computer Science, University of Zambia",
        ], "source": "original"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert result.is_valid


class TestEmptyOriginal:
    """When no original CV text is available, validation should pass with a note."""

    def test_empty_original_passes(self):
        sections = [{"heading": "Experience", "type": "experience", "items": [
            {"title": "Developer", "company": "AnyCompany", "dates": "2020-2024",
             "details": ["Did things"]},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, "")
        assert result.is_valid
        assert "skipped" in result.validation_summary.lower()


class TestValidationSummary:
    """The validation summary should be human-readable."""

    def test_all_supported_summary(self):
        sections = [{"heading": "Contact", "type": "contact",
                      "content": "Jane Smith", "source": "original"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert "supported" in result.validation_summary.lower()

    def test_unsupported_summary(self):
        sections = [{"heading": "Experience", "type": "experience", "items": [
            {"title": "Developer", "company": "Google", "dates": "2020-2024",
             "details": ["Did things"]},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert "unsupported" in result.validation_summary.lower()

    def test_to_dict_produces_valid_json(self):
        sections = [{"heading": "Contact", "type": "contact",
                      "content": "Jane Smith", "source": "original"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        d = result.to_dict()
        assert isinstance(d, dict)
        assert "is_valid" in d
        assert "unsupported_claims" in d
        assert "warnings" in d
        assert "findings" in d
        assert "validation_summary" in d
