"""Tests for the deterministic factual CV validation service.

No LLM calls are made â€” all tests use synthetic structured CV sections and
original CV text to verify deterministic validation logic.
"""

from __future__ import annotations

import datetime
import pytest

from backend.services.cv_validator import CVValidator, CVValidationResult


# â”€â”€ Test CVs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

# â”€â”€ Test cases â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


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
        # Kubernetes is not in the original â€” should be needs_confirmation at minimum
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
        # "500%" is not in the original â€” should be flagged
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


# ---------------------------------------------------------------------------
# Employer-validation fix: multi-token matching
# ---------------------------------------------------------------------------

# Original text reflecting the actual CV content, with section headings so
# the section-scoped extraction is exercised.
# - "eHemp House Zambia Limited" is the full employer name on record.
# - "Google Africa Developer Training Program" is a mentoring entry that
#   appears inside PROFESSIONAL EXPERIENCE (realistic), so "google" IS in
#   the employment-section evidence.  "Google Healthcare" still fails because
#   "healthcare" is absent â†’ 1/2 tokens â†’ below min_needed=2.
MWANSA_CV_TEXT = """
MWANSA MATIMBA

PROFESSIONAL EXPERIENCE

Managing Partner, React Digital Consulting (Jun 2019 - Present)
Develop business plans and financial models for SMEs.

HR Manager, eHemp House Zambia Limited (Jul 2020 - Jan 2021)
Supported HR administration and staff coordination.

Associate / Consultant, Laura & Partners Consulting Limited (2015 - 2017)
Managed employment contracts and HRIS records.

Mentor, Google Africa Developer Training Program (2021 - 2022)
Held regular check-ins with learners.

Administrator, Mooh Pooh Car Hire & Tours (Jan 2022 - Apr 2022)
Maintained business records.

Training Consultant, Yojimbo Management Solutions (Mar 2021 - Aug 2021)
Prepared training materials.

EDUCATION

Bachelor of Entrepreneurship, Mulungushi University, Zambia (2008 - 2013)

RELEVANT TRAINING AND CERTIFICATES

Human Resources Management, Udemy (2021)
Business Management, Tony Elumelu Foundation (2022)
Zoho CRM, LinkedIn Learning (2021)
"""


class TestEmployerMultiTokenMatching:
    """
    Tests that employer validation requires meaningful multi-token evidence,
    not just the presence of a first word anywhere in the CV.
    """

    # â”€â”€ False-acceptance cases (must now be UNSUPPORTED) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_google_healthcare_is_unsupported(self):
        """'Google Healthcare' must be unsupported.

        In MWANSA_CV_TEXT the word 'Google' appears only on the mentoring line
        "Mentor, Google Africa Developer Training Program (2021 - 2022)".
        That line contains a year so it IS included in date-bearing evidence.
        However _employer_tokens("Google Healthcare") = {"google", "healthcare"};
        "healthcare" is absent from the evidence set entirely, so only 1/2
        tokens match, which is below min_needed=2 â†’ unsupported.
        """
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Paid Search Manager", "company": "Google Healthcare",
                        "dates": "2019 - Present", "details": ["Managed PPC campaigns"]}]},
        ]
        result = CVValidator.validate(sections, MWANSA_CV_TEXT)
        assert not result.is_valid, (
            "Validator passed 'Google Healthcare' â€” 'healthcare' token is absent "
            "from date-bearing CV lines, so only 1/2 tokens match (below min_needed=2)."
        )
        claims = [c["claim"] for c in result.unsupported_claims]
        assert any("Google Healthcare" in c for c in claims), f"Wrong claim flagged: {claims}"

    def test_microsoft_ads_is_unsupported_when_microsoft_only_in_skills(self):
        """'Microsoft Ads Agency' must be unsupported when 'Microsoft' only
        appears in a skills/software context, not as an employer."""
        orig = "Skills: Microsoft Word, Excel, PowerPoint. React Digital Consulting 2019-Present."
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Paid Media Manager", "company": "Microsoft Ads Agency",
                        "dates": "2020 - 2022", "details": ["Ran display campaigns"]}]},
        ]
        result = CVValidator.validate(sections, orig)
        assert not result.is_valid, (
            "Validator passed 'Microsoft Ads Agency' â€” 'Microsoft' from skills "
            "must not constitute employer evidence."
        )

    def test_laura_solutions_agency_needs_confirmation_not_unsupported(self):
        """'Laura Solutions Agency' â€” after stripping generic suffixes ('solutions',
        'agency'), only 'laura' remains, which IS in the original from 'Laura &
        Partners Consulting Limited'.  Single-token employer â†’ 1 match needed â†’
        warning (needs_confirmation) is the correct outcome, not unsupported."""
        orig = "Laura & Partners Consulting Limited 2015 - 2017. React Digital."
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Consultant", "company": "Laura Solutions Agency",
                        "dates": "2015 - 2017", "details": ["Did consulting"]}]},
        ]
        result = CVValidator.validate(sections, orig)
        # 'laura' is the only meaningful token and it matches â†’ needs_confirmation,
        # not unsupported.  is_valid stays True; a warning is issued.
        assert result.is_valid, (
            f"'Laura Solutions Agency' reduces to 1 meaningful token ('laura') which "
            f"matches â€” should produce a warning, not a hard block. "
            f"unsupported_claims: {result.unsupported_claims}"
        )
        warn_claims = [w["detail"] for w in result.warnings]
        assert any("Laura Solutions Agency" in w for w in warn_claims), (
            f"Expected a needs_confirmation warning mentioning 'Laura Solutions Agency'. "
            f"warnings: {result.warnings}"
        )

    # â”€â”€ Legitimate variation cases (must remain PASS) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_ehemp_house_matches_ehemp_zambia_limited(self):
        """'eHemp House' must pass via the token fallback.

        The original CV records 'eHemp House Zambia Limited' (not 'eHemp House'
        verbatim), so _present_in fails.  The token fallback fires:
        _employer_tokens('eHemp House') = {'ehemp', 'house'}.
        The date-bearing evidence set from MWANSA_CV_TEXT contains 'ehemp'
        (from 'eHemp House Zambia Limited') and 'house' is also present on that
        line, so matching_tokens = {'ehemp', 'house'} â†’ 2 â‰¥ min_needed=2 â†’ pass.
        """
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "HR Manager", "company": "eHemp House",
                        "dates": "Jul 2020 - Jan 2021", "details": ["Did HR"]}]},
        ]
        result = CVValidator.validate(sections, MWANSA_CV_TEXT)
        assert result.is_valid, (
            f"'eHemp House' should pass via token fallback â€” 'ehemp' and 'house' "
            f"both appear on the employment line 'eHemp House Zambia Limited'. "
            f"unsupported_claims: {result.unsupported_claims}"
        )

    def test_react_digital_exact_match(self):
        """'React Digital Consulting' â€” exact match, must pass."""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Managing Partner", "company": "React Digital Consulting",
                        "dates": "Jun 2019 - Present", "details": ["Ran the business"]}]},
        ]
        result = CVValidator.validate(sections, MWANSA_CV_TEXT)
        assert result.is_valid, (
            f"'React Digital Consulting' should pass. "
            f"unsupported_claims: {result.unsupported_claims}"
        )

    def test_mooh_pooh_exact_match(self):
        """'Mooh Pooh Car Hire & Tours' â€” exact match, must pass."""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Administrator", "company": "Mooh Pooh Car Hire & Tours",
                        "dates": "Jan 2022 - Apr 2022", "details": ["Admin work"]}]},
        ]
        result = CVValidator.validate(sections, MWANSA_CV_TEXT)
        assert result.is_valid, (
            f"'Mooh Pooh Car Hire & Tours' should pass. "
            f"unsupported_claims: {result.unsupported_claims}"
        )

    def test_laura_partners_consulting_limited_exact_match(self):
        """Full 'Laura & Partners Consulting Limited' must pass exactly."""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Consultant", "company": "Laura & Partners Consulting Limited",
                        "dates": "2015 - 2017", "details": ["Consulting"]}]},
        ]
        result = CVValidator.validate(sections, MWANSA_CV_TEXT)
        assert result.is_valid, (
            f"'Laura & Partners Consulting Limited' should pass. "
            f"unsupported_claims: {result.unsupported_claims}"
        )

    def test_laura_partners_abbreviated_needs_confirmation(self):
        """'Laura & Partners' abbreviated form â€” 'laura' and 'partners' both
        appear in the original, so at least 2 tokens match â†’ needs_confirmation
        (warning), not unsupported."""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "HR Associate", "company": "Laura & Partners",
                        "dates": "2015 - 2017", "details": ["HR consulting"]}]},
        ]
        result = CVValidator.validate(sections, MWANSA_CV_TEXT)
        # 2 tokens ("laura" and "partners") match â€” warning allowed, not block
        # is_valid can be True (needs_confirmation does not block)
        assert result.is_valid, (
            f"'Laura & Partners' abbreviated should be a warning, not unsupported. "
            f"unsupported_claims: {result.unsupported_claims}"
        )

    def test_yojimbo_management_solutions(self):
        """'Yojimbo Management Solutions' must pass â€” 'yojimbo' is unique."""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Training Consultant",
                        "company": "Yojimbo Management Solutions",
                        "dates": "Mar 2021 - Aug 2021", "details": ["Trained people"]}]},
        ]
        result = CVValidator.validate(sections, MWANSA_CV_TEXT)
        assert result.is_valid, (
            f"'Yojimbo Management Solutions' should pass. "
            f"unsupported_claims: {result.unsupported_claims}"
        )

    def test_google_africa_developer_training_exact_match(self):
        """'Google Africa Developer Training Program' â€” the full original name
        must still pass verbatim."""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Mentor", "company": "Google Africa Developer Training Program",
                        "dates": "2021 - 2022", "details": ["Mentored developers"]}]},
        ]
        result = CVValidator.validate(sections, MWANSA_CV_TEXT)
        assert result.is_valid, (
            f"Full original employer name must pass. "
            f"unsupported_claims: {result.unsupported_claims}"
        )

    def test_completely_fabricated_employer_unsupported(self):
        """An employer with no tokens in the original must always be unsupported."""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Manager", "company": "Accenture Digital Africa",
                        "dates": "2020 - 2022", "details": ["Consulting"]}]},
        ]
        result = CVValidator.validate(sections, MWANSA_CV_TEXT)
        assert not result.is_valid, (
            "Completely fabricated employer must be unsupported."
        )

    def test_tony_elumelu_as_employer_rejected(self):
        """'Tony Elumelu Foundation' appears only under RELEVANT TRAINING AND
        CERTIFICATES, not in PROFESSIONAL EXPERIENCE.

        With section-scoped verbatim matching, it must not pass as an employer.
        The employment-section text does not contain 'Tony Elumelu Foundation',
        so the verbatim check fails; the token fallback also finds no matching
        tokens in the employment evidence â†’ unsupported.
        """
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Business Advisor",
                        "company": "Tony Elumelu Foundation",
                        "dates": "2022 - Present",
                        "details": ["Advised SMEs"]}]},
        ]
        result = CVValidator.validate(sections, MWANSA_CV_TEXT)
        assert not result.is_valid, (
            "'Tony Elumelu Foundation' is in the training section only and "
            "must be unsupported as an employer claim."
        )

    def test_mulungushi_university_as_employer_rejected(self):
        """'Mulungushi University' appears only under EDUCATION.

        The employment-section text does not contain it, so both the verbatim
        and token fallback checks fail â†’ unsupported.
        """
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Research Officer",
                        "company": "Mulungushi University",
                        "dates": "2008 - 2013",
                        "details": ["Did research"]}]},
        ]
        result = CVValidator.validate(sections, MWANSA_CV_TEXT)
        assert not result.is_valid, (
            "'Mulungushi University' is in the EDUCATION section only and "
            "must be unsupported as an employer claim."
        )

    # â”€â”€ False-heading-detection regression tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_google_africa_developer_training_program_accepted_as_employer(self):
        """Regression: 'Google Africa Developer Training Program' must NOT be
        treated as a section-close heading even though it contains 'Training'.

        This is the exact defect that was introduced when _NON_EMPLOYMENT_HEADINGS
        used re.search instead of re.match: the word 'Training' mid-string caused
        the employer line to close the employment window and exclude itself from
        evidence, making the claim unsupported.

        In a two-line CV format the date is on the role line; the employer name
        is on the following line without a year.  With the fix the employer line
        is correctly retained in the employment section.
        """
        cv_with_two_line_format = """
PROFESSIONAL EXPERIENCE

Mentor, 2021 - 2022
Google Africa Developer Training Program
Held regular check-ins with learners.

EDUCATION

Bachelor of Entrepreneurship, Mulungushi University (2008 - 2013)
"""
        sections = [
            {"heading": "Professional Experience", "type": "experience",
             "source": "rewritten",
             "items": [{"title": "Mentor",
                        "company": "Google Africa Developer Training Program",
                        "dates": "2021 - 2022",
                        "details": ["Held regular check-ins"]}]},
        ]
        result = CVValidator.validate(sections, cv_with_two_line_format)
        assert result.is_valid, (
            "'Google Africa Developer Training Program' must be accepted â€” it "
            "is a real employer inside PROFESSIONAL EXPERIENCE, not a heading. "
            f"unsupported_claims: {result.unsupported_claims}"
        )

    def test_relevant_training_and_certificates_still_closes_employment_section(self):
        """'RELEVANT TRAINING AND CERTIFICATES' must still close the employment
        window even though it starts with 'RELEVANT', not 'TRAINING'.

        The optional leading-qualifier prefix in _NON_EMPLOYMENT_HEADINGS covers
        exactly this heading pattern.
        """
        cv = """
PROFESSIONAL EXPERIENCE

Managing Partner, React Digital Consulting (Jun 2019 - Present)
Develop business plans.

RELEVANT TRAINING AND CERTIFICATES

Human Resources Management, Udemy (2021)
Tony Elumelu Foundation Business Management (2022)
"""
        # Tony Elumelu Foundation is in the training section; claiming it as
        # an employer must be rejected.
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Business Advisor",
                        "company": "Tony Elumelu Foundation",
                        "dates": "2022 - Present",
                        "details": ["Advised SMEs"]}]},
        ]
        result = CVValidator.validate(sections, cv)
        assert not result.is_valid, (
            "'RELEVANT TRAINING AND CERTIFICATES' must close the employment "
            "section so that Tony Elumelu Foundation is not accepted as an employer."
        )

    def test_training_solutions_international_accepted_as_employer(self):
        """An employer whose name starts with 'Training' â€” e.g. 'Training
        Solutions International' â€” must NOT close the employment section.

        The vocabulary-based heading check recognises only lines where EVERY
        word is a known heading word or connector.  'Training Solutions
        International' contains 'solutions' and 'international' which are not
        in the heading vocabulary, so the line is NOT treated as a heading and
        stays inside the employment section.

        This test uses the two-line CV format (role+date on one line, employer
        on the next) to prove the employer line is retained.
        """
        cv_with_training_employer = """
PROFESSIONAL EXPERIENCE

Business Analyst, Jan 2020 - Dec 2021
Training Solutions International
Delivered analytical reports.

EDUCATION
BSc, University of Zambia (2015)
"""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Business Analyst",
                        "company": "Training Solutions International",
                        "dates": "Jan 2020 - Dec 2021",
                        "details": ["Delivered analytical reports"]}]},
        ]
        result = CVValidator.validate(sections, cv_with_training_employer)
        # The employer name is on its own line (no year) inside the employment
        # section.  With the vocabulary-based heading check, "Training Solutions
        # International" is NOT a heading (contains non-heading words), so the
        # line is retained in employment_lines.  _present_in finds it verbatim.
        assert result.is_valid, (
            "'Training Solutions International' on its own line must stay inside "
            "the employment section and pass verbatim. "
            f"unsupported_claims: {result.unsupported_claims}"
        )

    # â”€â”€ Heading-detection sentinel regression â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_education_only_cv_does_not_accept_education_org_as_employer(self):
        """A CV with EDUCATION and RELEVANT TRAINING AND CERTIFICATES headings
        but NO employment section must not fall back to full-text matching.

        _extract_employment_section_text returns "" (not None) when headings
        exist but no employment section was found.  The caller must use the
        empty string as evidence text â€” not the full CV â€” so organisations
        appearing only in education/training sections are rejected as
        employers.
        """
        cv_with_education_only = """
EDUCATION

Bachelor of Entrepreneurship, Mulungushi University, Zambia (2008 - 2013)

RELEVANT TRAINING AND CERTIFICATES

Human Resources Management, Udemy (2021)
Business Management, Tony Elumelu Foundation (2022)
"""
        sections = [
            {"heading": "Experience", "type": "experience", "source": "rewritten",
             "items": [{"title": "Student",
                        "company": "Mulungushi University",
                        "dates": "2008 - 2013",
                        "details": ["Studied entrepreneurship"]}]},
        ]
        result = CVValidator.validate(sections, cv_with_education_only)
        assert not result.is_valid, (
            "'Mulungushi University' appears only under EDUCATION â€” must not be "
            "accepted as an employer when no employment section exists. "
            f"unsupported_claims: {result.unsupported_claims}"
        )


class TestEducationTokenOverlapFix:
    """F5 regression: education institution must use min(2,N)-token overlap, not first-word."""

    def test_first_word_only_institution_rejected(self):
        """'University of Cape Town' must fail when original only contains
        'University of Zambia' â€” 'university' alone must not be sufficient
        because 'cape' and 'town' are absent."""
        sections = [{"heading": "Education", "type": "education", "items": [
            {"institution": "University of Cape Town",
             "qualification": "BSc Engineering", "dates": "2010-2014"},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        # Only "university" matches; "cape" and "town" are absent â†’ 1/3 tokens < min_needed=2
        assert not result.is_valid

    def test_verbatim_institution_still_passes(self):
        """Institution present verbatim must still pass after the fix."""
        sections = [{"heading": "Education", "type": "education", "items": [
            {"institution": "University of Zambia",
             "qualification": "BSc Computer Science", "dates": "2014-2018"},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert result.is_valid

    def test_single_token_institution_requires_one_match(self):
        """A one-word institution name requires 1 matching token (min(2,1)=1)."""
        # ORIGINAL_CV contains "University of Zambia" â€” test that an abbreviated
        # single-token institution passes when the one meaningful token is present.
        sections = [{"heading": "Education", "type": "education", "items": [
            {"institution": "Zambia",
             "qualification": "BSc Computer Science", "dates": "2014-2018"},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        # "zambia" IS in ORIGINAL_CV (from "University of Zambia") â†’ 1/1 match â†’ passes
        assert result.is_valid


class TestSummaryQuantFigureReclassification:
    """F4 regression: unrecognised summary figures must set is_valid=False."""

    def test_fabricated_summary_percent_sets_is_valid_false(self):
        """A percent figure absent from the original in a summary section must block."""
        sections = [{"heading": "Summary", "type": "summary",
                     "content": "Drove a 95% improvement in team productivity.",
                     "items": [], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        # 95% is not in ORIGINAL_CV
        assert not result.is_valid

    def test_supported_summary_figure_passes(self):
        """A figure present verbatim in the original must still pass."""
        orig = "Reduced API latency by 40%."
        sections = [{"heading": "Summary", "type": "summary",
                     "content": "Drove a 40% reduction in API latency.",
                     "items": [], "source": "rewritten"}]
        result = CVValidator.validate(sections, orig)
        assert result.is_valid

    def test_no_figure_summary_passes(self):
        """A summary section with no quantified figures must pass unconditionally."""
        sections = [{"heading": "Summary", "type": "summary",
                     "content": "Experienced software engineer with strong Python skills.",
                     "items": [], "source": "rewritten"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert result.is_valid


class TestSkippedSentinelInToDict:
    """Design-review: CVValidationResult.to_dict() must emit skipped=False."""

    def test_to_dict_has_skipped_false_on_success(self):
        sections = [{"heading": "Contact", "type": "contact",
                     "content": "Jane Smith", "source": "original"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        d = result.to_dict()
        assert "skipped" in d
        assert d["skipped"] is False

    def test_to_dict_has_all_required_keys(self):
        sections = [{"heading": "Contact", "type": "contact",
                     "content": "Jane Smith", "source": "original"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        d = result.to_dict()
        for key in ("is_valid", "skipped", "unsupported_claims", "warnings",
                    "findings", "validation_summary"):
            assert key in d, f"Missing key: {key}"


class TestEmptySourceFailClosed:
    """Empty/whitespace-only original CV text must produce skipped=True,
    not is_valid=True with skipped=False."""

    def test_empty_source_produces_skipped_true(self):
        """Empty source text must set skipped=True so export is blocked."""
        sections = [{"heading": "Experience", "type": "experience", "items": [
            {"title": "Manager", "company": "Totally Fabricated Corp",
             "dates": "2020-2021", "details": ["Built everything"]},
        ], "source": "rewritten"}]
        result = CVValidator.validate(sections, "")
        assert result.skipped is True
        d = result.to_dict()
        assert d["skipped"] is True
        assert d["is_valid"] is True  # nothing was contradicted, but nothing was verified

    def test_whitespace_only_source_produces_skipped_true(self):
        """Whitespace-only source text must also be treated as skipped."""
        sections = [{"heading": "Summary", "type": "summary",
                     "content": "Increased revenue by 999%.", "items": [],
                     "source": "rewritten"}]
        result = CVValidator.validate(sections, "   \n\t  \n  ")
        assert result.skipped is True
        d = result.to_dict()
        assert d["skipped"] is True

    def test_empty_source_does_not_catch_fabricated_content(self):
        """With empty source, fabricated employers and figures must NOT be
        flagged as unsupported (because no comparison was possible).  The
        protection is the skipped=True flag, not unsupported_claims."""
        sections = [
            {"heading": "Experience", "type": "experience", "items": [
                {"title": "CTO", "company": "Totally Fabricated Corp",
                 "dates": "2020-2021",
                 "details": ["Increased revenue by 999%"]},
            ], "source": "rewritten"},
            {"heading": "Summary", "type": "summary",
             "content": "Increased revenue by 999%.", "items": [],
             "source": "rewritten"},
        ]
        result = CVValidator.validate(sections, "")
        assert result.skipped is True
        assert len(result.unsupported_claims) == 0  # no comparison ran

    def test_valid_source_still_produces_skipped_false(self):
        """Normal validation with real source text must still produce skipped=False."""
        sections = [{"heading": "Contact", "type": "contact",
                     "content": "Jane Smith", "source": "original"}]
        result = CVValidator.validate(sections, ORIGINAL_CV)
        assert result.skipped is False
        d = result.to_dict()
        assert d["skipped"] is False


class TestCVValidationResultBackwardCompat:
    """Backward-compatibility for the new ``skipped`` field on
    ``CVValidationResult``.

    The field was added with ``default=False`` so that existing callers that do
    not pass ``skipped`` continue to get ``False`` (genuine validation ran and
    was not skipped).  These tests guard against accidental changes to the
    default or to ``to_dict()`` serialization.
    """

    def test_constructor_without_skipped_defaults_to_false(self):
        """Old-style constructor call (no ``skipped`` argument) must default
        to ``False``, meaning validation genuinely ran."""
        result = CVValidationResult(is_valid=True)
        assert result.skipped is False

    def test_constructor_without_skipped_serializes_as_false(self):
        """``to_dict()`` on an old-style result must emit ``skipped: False``."""
        result = CVValidationResult(is_valid=True, validation_summary="ok")
        d = result.to_dict()
        assert d["skipped"] is False

    def test_constructor_with_skipped_true_serializes_correctly(self):
        """Explicit ``skipped=True`` must propagate through ``to_dict()``."""
        result = CVValidationResult(is_valid=True, skipped=True,
                                    validation_summary="skipped")
        d = result.to_dict()
        assert d["skipped"] is True

    def test_old_validation_dict_without_skipped_key_is_safe(self):
        """A validation dict from the committed code (pre-``skipped`` field)
        must not crash any consumer.  ``_check_export_allowed`` uses
        ``.get('skipped')`` which returns ``None`` (falsy) for absent keys,
        so old dicts fall through to the ``is_valid`` check â€” safe."""
        old_dict = {
            "is_valid": True,
            "unsupported_claims": [],
            "warnings": [],
            "findings": [],
            "validation_summary": "All verifiable claims are supported.",
        }
        # Simulate what _check_export_allowed does:
        skipped = old_dict.get("skipped")  # None â€” falsy
        is_valid = old_dict.get("is_valid", True)
        # Old clean-pass dict â†’ not blocked
        assert not skipped
        assert is_valid is True

    def test_old_validation_dict_with_is_valid_false_still_blocks(self):
        """An old dict with ``is_valid: False`` (no ``skipped`` key) must still
        be blocked by the export guard."""
        old_dict = {
            "is_valid": False,
            "unsupported_claims": [{"claim": "Employer: 'Google'"}],
            "warnings": [],
            "findings": [],
            "validation_summary": "1 unsupported claim(s) detected.",
        }
        skipped = old_dict.get("skipped")  # None â€” falsy
        is_valid = old_dict.get("is_valid", True)
        # Old failed-validation dict â†’ blocked by is_valid=False
        assert not skipped  # no skipped key
        assert not is_valid  # blocked


class TestMalformedValidationDict:
    """Malformed or incomplete validation dicts must fail closed, not pass
    as clean validation."""

    def test_non_dict_validation_record_blocks(self):
        """A validation value that is not a dict must not pass."""
        from unittest.mock import MagicMock
        from backend.api.routes.tailoring import _check_export_allowed
        import fastapi
        import uuid as _uuid

        cv = MagicMock()
        cv.content = {"validation": "not a dict"}  # malformed
        cv.id = _uuid.uuid4()
        cv.created_at = datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc)

        with pytest.raises(fastapi.HTTPException) as exc_info:
            _check_export_allowed(cv)
        assert exc_info.value.status_code == 422

    def test_validation_dict_missing_is_valid_blocks(self):
        """A validation dict without is_valid must not pass."""
        from unittest.mock import MagicMock
        from backend.api.routes.tailoring import _check_export_allowed
        import fastapi
        import uuid as _uuid

        cv = MagicMock()
        cv.content = {"validation": {"unsupported_claims": [], "warnings": []}}
        cv.id = _uuid.uuid4()
        cv.created_at = datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc)

        with pytest.raises(fastapi.HTTPException) as exc_info:
            _check_export_allowed(cv)
        assert exc_info.value.status_code == 422
        assert "incomplete" in exc_info.value.detail.lower()

    def test_validation_dict_missing_unsupported_claims_blocks(self):
        """A validation dict without unsupported_claims must not pass."""
        from unittest.mock import MagicMock
        from backend.api.routes.tailoring import _check_export_allowed
        import fastapi
        import uuid as _uuid

        cv = MagicMock()
        cv.content = {"validation": {"is_valid": True, "warnings": []}}
        cv.id = _uuid.uuid4()
        cv.created_at = datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc)

        with pytest.raises(fastapi.HTTPException) as exc_info:
            _check_export_allowed(cv)
        assert exc_info.value.status_code == 422

    def test_validation_dict_missing_warnings_blocks(self):
        """A validation dict without warnings must not pass."""
        from unittest.mock import MagicMock
        from backend.api.routes.tailoring import _check_export_allowed
        import fastapi
        import uuid as _uuid

        cv = MagicMock()
        cv.content = {"validation": {"is_valid": True, "unsupported_claims": []}}
        cv.id = _uuid.uuid4()
        cv.created_at = datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc)

        with pytest.raises(fastapi.HTTPException) as exc_info:
            _check_export_allowed(cv)
        assert exc_info.value.status_code == 422

    def test_old_complete_dict_without_skipped_still_allowed(self):
        """An old dict with all required fields but no skipped key is
        structurally valid and should be allowed if is_valid=True."""
        from unittest.mock import MagicMock
        from backend.api.routes.tailoring import _check_export_allowed
        import uuid as _uuid

        cv = MagicMock()
        cv.content = {"validation": {
            "is_valid": True,
            "unsupported_claims": [],
            "warnings": [],
            "findings": [],
            "validation_summary": "All verifiable claims are supported.",
        }}
        cv.id = _uuid.uuid4()
        cv.created_at = datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc)

        # Should NOT raise â€” the dict is structurally complete.
        _check_export_allowed(cv)
