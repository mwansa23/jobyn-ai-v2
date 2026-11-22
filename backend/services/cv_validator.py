"""Factual validation service for generated tailored CVs.

This module provides a **deterministic**, LLM-free validation layer that
compares each generated CV section against the original CV text.  It
classifies every claim according to whether it can be traced back to the
source document.

Claim classifications
---------------------
- ``supported``           – The claim (employer, title, date, skill, cert,
                           figure) is literally present in the original CV.
- ``reasonable_rewording`` – The substance is present but the wording has
                            been improved (checked heuristically via token
                            overlap).
- ``needs_confirmation``  – Partial match — the evidence exists but is
                            ambiguous (e.g. a skill inferred from a tool
                            name, a date range that partially matches).
- ``unsupported``         – No evidence found in the original CV text.
- ``contradicted``        – The claim directly conflicts with a verifiable
                            fact in the original (e.g. a different employer
                            name for the same date range).

The validator never calls the LLM.  It uses regular expressions and simple
string operations.  False-positive rates are acceptable for an MVP: the goal
is to catch material fabrications, not to grade every word.

Usage
-----
::

    from backend.services.cv_validator import CVValidator

    result = CVValidator.validate(generated_sections, original_cv_text)
    # result.is_valid → False if any unsupported/contradicted claim found
    # result.unsupported_claims → list[dict] with details
    # result.validation_summary → human-readable summary
    # result.findings → full per-section findings

The result dict can be stored directly under ``TailoredCV.content["validation"]``
without any schema change — the ``content`` column is ``JSON``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Years: 1960–2099
_YEAR_RE = re.compile(r"\b(19[6-9]\d|20[0-2]\d)\b")

# Quantified achievements: a number followed by %, x, $, £, K, M, or text suffixes.
# Note: \b is only placed at the start (before the digit), not the end,
# because %, $, £ are non-word characters where \b won't fire.
_QUANT_RE = re.compile(
    r"\b(\d[\d,\.]*\s*(?:%|\$|£|USD|ZMW|K|M|B|mn|bn|million|billion|times|fold))",
    re.IGNORECASE,
)

# Certification / course keywords to tighten scope
_CERT_SIGNALS = re.compile(
    r"\b(certified|certification|certificate|diploma|degree|license|licence|"
    r"AWS|Azure|GCP|PMP|PRINCE2|CISSP|CPA|ACCA|CIMA|CFA|ITIL|CompTIA|Cisco|"
    r"Oracle|Salesforce|HubSpot|Google Analytics)\b",
    re.IGNORECASE,
)


def _tokens(text: str) -> set[str]:
    """Return lowercase word tokens from *text*, removing stopwords."""
    _STOPWORDS = {
        "a", "an", "the", "and", "or", "in", "at", "of", "to", "for",
        "with", "on", "by", "from", "as", "is", "was", "were", "be",
        "been", "being", "have", "has", "had", "do", "does", "did",
        "i", "my", "we", "our", "you", "your", "their", "its",
        "this", "that", "which", "who", "how", "when", "where", "if",
    }
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _overlap_ratio(a: str, b: str) -> float:
    """Return Jaccard-style overlap of meaningful tokens between *a* and *b*."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    intersection = ta & tb
    union = ta | tb
    return len(intersection) / len(union)


def _present_in(value: str, text: str) -> bool:
    """Return True if *value* (case-insensitive) appears as a substring of *text*."""
    return bool(value.strip()) and value.strip().lower() in text.lower()


# Company-name suffixes that are too generic to be used as matching evidence.
_COMPANY_SUFFIXES = frozenset({
    "ltd", "limited", "inc", "incorporated", "llc", "plc", "co", "corp",
    "corporation", "group", "holdings", "services", "solutions", "consulting",
    "consultants", "associates", "partners", "agency", "enterprises", "company",
    "international", "global", "africa", "zambia", "lusaka",
})


def _employer_tokens(employer: str) -> set[str]:
    """Return meaningful lowercase tokens from an employer name.

    Strips generic company-suffix words (Ltd, Limited, Inc, etc.) and common
    geographic words so that the remaining tokens are name-specific.  Requires
    tokens of at least 3 characters.
    """
    words = re.findall(r"[a-z0-9]+", employer.lower())
    return {
        w for w in words
        if len(w) >= 3 and w not in _COMPANY_SUFFIXES and w not in _tokens("")
    }


def _extract_employer_tokens_from_cv(cv_text: str) -> set[str]:
    """Return meaningful tokens drawn only from date-bearing lines inside the
    employment-history section of the original CV.

    Uses :func:`_extract_employment_section_text` to restrict to the employment
    window, then collects tokens only from lines that contain a 4-digit year
    (employer/role header lines).  Falls back to all date-bearing lines in the
    full CV when no employment section heading is found.
    """
    employment_text = _extract_employment_section_text(cv_text)

    if employment_text is None:
        # Headingless CV — fall back to all date-bearing lines in the full text.
        tokens = set()
        for line in cv_text.splitlines():
            stripped = line.strip()
            if stripped and _YEAR_RE.search(stripped):
                tokens |= _employer_tokens(stripped)
        return tokens

    # Employment section text (may be empty if headings exist but no
    # employment section was found — returns empty token set, which is
    # correct: no employer evidence should be available).
    tokens = set()
    for line in employment_text.splitlines():
        stripped = line.strip()
        if stripped and _YEAR_RE.search(stripped):
            tokens |= _employer_tokens(stripped)
    return tokens


def _extract_employment_section_text(cv_text: str) -> str:
    """Return only the lines that belong to identifiable employment-history
    sections of the original CV.

    Uses the same section-heading detection as the token extractor.  Returns
    an empty string when no employment section heading is found, so callers
    can fall back to the full text.
    """
    # Known employment section headings (case-insensitive match at start of line)
    _EMPLOYMENT_HEADINGS = re.compile(
        r"^(professional\s+experience|work\s+experience|employment\s+history"
        r"|career\s+history|experience)\s*$",
        re.IGNORECASE,
    )
    # Vocabulary of words allowed in non-employment section headings.
    # A short non-year non-bullet line is treated as a non-employment heading
    # ONLY if every word it contains is in this set.  This prevents employer
    # names that happen to start with a heading keyword — e.g. "Training
    # Solutions International", "Education First", "Skills Academy" — from
    # being mistaken for section headings, while still recognising real
    # headings like "EDUCATION", "RELEVANT TRAINING AND CERTIFICATES", and
    # "PROFESSIONAL DEVELOPMENT".
    _HEADING_WORDS = frozenset({
        "education", "educational", "qualifications", "qualification", "academic",
        "certification", "certifications", "certificates", "certificate",
        "training", "courses", "course",
        "professional", "development",
        "languages", "language",
        "skills", "skill",
        "references", "reference",
        "volunteer", "volunteering",
        "awards", "award",
        "publications", "publication",
        "projects", "project",
        "summary", "profile", "objective",
        "competencies", "competency",
        "interests", "interest",
        "hobbies", "hobby",
        "memberships", "membership",
        "relevant", "additional", "other",
        "core", "key", "special",
        "history", "background", "records",
    })
    _HEADING_CONNECTORS = frozenset({
        "and", "the", "of", "for", "&",
    })

    employment_lines: list[str] = []
    in_employment_section = False
    found_any_heading = False

    for line in cv_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Detect section headings: short lines, no year, no bullet marker.
        is_short = len(stripped) <= 60
        has_year = bool(_YEAR_RE.search(stripped))
        is_bullet = stripped[:2] in ("ò ", "• ", "- ", "* ") or stripped.startswith("o ")

        if is_short and not has_year and not is_bullet:
            # Strip a trailing colon before heading pattern matching — common
            # in free-form CVs ("Professional Experience:", "Education:").
            stripped_heading = stripped.rstrip(":").strip()
            if _EMPLOYMENT_HEADINGS.match(stripped_heading):
                in_employment_section = True
                found_any_heading = True
                continue
            # Check whether this line is a non-employment section heading.
            # A line qualifies only if EVERY word is a known heading word or
            # a common connector.  This prevents employer names like
            # "Training Solutions International" from closing the window.
            words = re.findall(r"[a-z0-9&]+", stripped_heading.lower())
            if words and all(
                w in _HEADING_WORDS or w in _HEADING_CONNECTORS
                for w in words
            ):
                in_employment_section = False
                found_any_heading = True
                continue

        if in_employment_section:
            employment_lines.append(stripped)

    if not found_any_heading:
        return None  # headingless CV — caller falls back to full text

    return "\n".join(employment_lines)


# ── Placeholder detection ────────────────────────────────────────────────────

# Common placeholder values that LLMs emit when they don't have real data.
_PLACEHOLDER_PATTERNS = [
    re.compile(r"^\[?your\s+", re.IGNORECASE),
    re.compile(r"^job\s*title\s*$", re.IGNORECASE),
    re.compile(r"^company\s*(name)?\s*$", re.IGNORECASE),
    re.compile(r"^dates?\s*$", re.IGNORECASE),
    re.compile(r"^date\s*(range)?\s*$", re.IGNORECASE),
    re.compile(r"^placeholder", re.IGNORECASE),
    re.compile(r"^n/?a\s*$", re.IGNORECASE),
    re.compile(r"^tbd\s*$", re.IGNORECASE),
    re.compile(r"^lorem\s+ipsum", re.IGNORECASE),
    re.compile(r"^\[.*\]\s*$"),  # bracket-only text like [Company Name]
    re.compile(r"^enter\s+your\s+", re.IGNORECASE),
]


def _is_placeholder(value: str) -> bool:
    """Return True if *value* looks like a placeholder rather than real data."""
    stripped = value.strip()
    if not stripped:
        return True
    return any(p.search(stripped) for p in _PLACEHOLDER_PATTERNS)


# ── Experience-duration claim detection ──────────────────────────────────────

# Matches patterns like "3+ years of experience" or "5 years of paid-search
# experience" or "3+ years of business development expertise".  Allows a
# domain keyword (one or more words/hyphens) between "of" and the final noun.
_DURATION_CLAIM_RE = re.compile(
    r"\b(\d+\+?\s*(?:years?|months?|yrs?)\s+of\s+[\w\s,-]+?(?:experience|expertise|background))",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Validation result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CVValidationResult:
    """The outcome of validating one generated CV against an original."""

    is_valid: bool
    """False when any claim is classified as ``unsupported`` or ``contradicted``."""

    skipped: bool = False
    """True when validation could not run (empty/unavailable source text)."""

    unsupported_claims: list[dict[str, Any]] = field(default_factory=list)
    """Each entry: ``{section, item_index, claim, classification, detail}``."""

    warnings: list[dict[str, Any]] = field(default_factory=list)
    """``needs_confirmation`` items — not blocking but surface to the candidate."""

    findings: list[dict[str, Any]] = field(default_factory=list)
    """Full per-section finding list including ``supported`` items."""

    validation_summary: str = ""
    """One or two sentences suitable for display in the UI."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "skipped": self.skipped,
            "unsupported_claims": self.unsupported_claims,
            "warnings": self.warnings,
            "findings": self.findings,
            "validation_summary": self.validation_summary,
        }


# ---------------------------------------------------------------------------
# Main validator
# ---------------------------------------------------------------------------

class CVValidator:
    """Deterministic factual validator for generated CV sections.

    All public methods are **static** — instantiate only when you need a
    custom configuration.
    """

    # Minimum token-overlap ratio to call a claim a "reasonable rewording"
    # rather than unsupported.
    REWORDING_THRESHOLD: float = 0.35

    # ---------------------------------------------------------------------------
    # Public entry point
    # ---------------------------------------------------------------------------

    @classmethod
    def validate(
        cls,
        sections: list[dict[str, Any]],
        original_cv_text: str,
    ) -> CVValidationResult:
        """Validate *sections* against *original_cv_text*.

        Args:
            sections:          The ``sections`` list from a generated
                               :class:`TailoredCV` ``content`` dict.
            original_cv_text:  Plain text of the original uploaded CV as
                               extracted from the uploaded document.

        Returns:
            :class:`CVValidationResult`.
        """
        if not original_cv_text or not original_cv_text.strip():
            # No original to compare against — mark as skipped so the export
            # guard blocks it.  is_valid stays True (nothing was contradicted)
            # but skipped=True signals that no validation actually ran.
            return CVValidationResult(
                is_valid=True,
                skipped=True,
                validation_summary=(
                    "Original CV text is unavailable; factual validation was skipped."
                ),
            )

        orig = original_cv_text.strip()
        orig_years = set(_YEAR_RE.findall(orig))
        orig_quant = set(q.lower() for q in _QUANT_RE.findall(orig))

        all_findings: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        for sec_idx, section in enumerate(sections):
            if not isinstance(section, dict):
                continue

            sec_heading = section.get("heading") or section.get("type") or f"Section {sec_idx}"
            sec_type = section.get("type", "other")
            source = section.get("source", "rewritten")
            items = section.get("items") or []
            content = (section.get("content") or "").strip()

            # ── Contact section is always trusted (name, phone, email) ──────
            if sec_type == "contact":
                all_findings.append({
                    "section": sec_heading,
                    "section_index": sec_idx,
                    "classification": "supported",
                    "detail": "Contact section preserved from the original CV.",
                })
                continue

            # ── Experience items ─────────────────────────────────────────────
            if sec_type == "experience":
                for item_idx, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    finding = cls._validate_experience_item(
                        item, item_idx, sec_heading, orig, orig_years, orig_quant
                    )
                    all_findings.append(finding)
                    if finding["classification"] in ("unsupported", "contradicted"):
                        unsupported.append(finding)
                    elif finding["classification"] == "needs_confirmation":
                        warnings.append(finding)
                continue

            # ── Education items ──────────────────────────────────────────────
            if sec_type == "education":
                for item_idx, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    finding = cls._validate_education_item(
                        item, item_idx, sec_heading, orig, orig_years
                    )
                    all_findings.append(finding)
                    if finding["classification"] in ("unsupported", "contradicted"):
                        unsupported.append(finding)
                    elif finding["classification"] == "needs_confirmation":
                        warnings.append(finding)
                continue

            # ── Certifications ───────────────────────────────────────────────
            if sec_type == "certifications":
                for item_idx, item in enumerate(items):
                    text = item if isinstance(item, str) else (
                        item.get("title") or item.get("name") or ""
                    ) if isinstance(item, dict) else str(item)
                    finding = cls._validate_certification(
                        text, item_idx, sec_heading, orig
                    )
                    all_findings.append(finding)
                    if finding["classification"] in ("unsupported", "contradicted"):
                        unsupported.append(finding)
                    elif finding["classification"] == "needs_confirmation":
                        warnings.append(finding)
                continue

            # ── Skills section ───────────────────────────────────────────────
            if sec_type == "skills":
                for item_idx, item in enumerate(items):
                    skill_text = item if isinstance(item, str) else str(item)
                    finding = cls._validate_skill(
                        skill_text, item_idx, sec_heading, orig
                    )
                    all_findings.append(finding)
                    if finding["classification"] in ("unsupported", "contradicted"):
                        unsupported.append(finding)
                    elif finding["classification"] == "needs_confirmation":
                        warnings.append(finding)
                continue

            # ── Summary and other sections ───────────────────────────────────
            # Check for quantified figures that don't appear in the original.
            quant_findings = cls._validate_quantified_figures(
                content, sec_heading, orig_quant
            )
            all_findings.extend(quant_findings)
            for f in quant_findings:
                if f["classification"] in ("unsupported", "contradicted"):
                    unsupported.append(f)
                elif f["classification"] == "needs_confirmation":
                    warnings.append(f)

            if not quant_findings:
                all_findings.append({
                    "section": sec_heading,
                    "section_index": sec_idx,
                    "classification": "supported",
                    "detail": "No verifiable claims identified; content accepted.",
                })

        # ── Build result ─────────────────────────────────────────────────────
        is_valid = len(unsupported) == 0

        if unsupported:
            summary = (
                f"{len(unsupported)} unsupported claim(s) detected — "
                "review before accepting this CV version."
            )
        elif warnings:
            summary = (
                f"{len(warnings)} claim(s) need candidate confirmation. "
                "All other content is supported by the original CV."
            )
        else:
            summary = (
                "All verifiable claims are supported by the original CV."
            )

        return CVValidationResult(
            is_valid=is_valid,
            unsupported_claims=unsupported,
            warnings=warnings,
            findings=all_findings,
            validation_summary=summary,
        )

    # ---------------------------------------------------------------------------
    # Per-claim validators
    # ---------------------------------------------------------------------------

    @classmethod
    def _validate_experience_item(
        cls,
        item: dict[str, Any],
        item_idx: int,
        section: str,
        orig: str,
        orig_years: set[str],
        orig_quant: set[str],
    ) -> dict[str, Any]:
        employer = (
            item.get("company")
            or item.get("organisation")
            or item.get("employer")
            or ""
        ).strip()
        title = (item.get("title") or item.get("role") or "").strip()
        dates = (item.get("dates") or item.get("date") or "").strip()
        details = item.get("details") or item.get("bullets") or []

        issues: list[str] = []

        # ── Placeholder detection ──────────────────────────────────────────
        for field_name, field_value in [("title", title), ("employer", employer), ("dates", dates)]:
            if field_value and _is_placeholder(field_value):
                return {
                    "section": section,
                    "item_index": item_idx,
                    "claim": f"{field_name}: {field_value!r}",
                    "classification": "unsupported",
                    "detail": f"Placeholder value for {field_name} — not real candidate data.",
                }

        # Employer check
        # When the CV has recognisable employment-section headings, restrict
        # both the verbatim check and the token fallback to that section's
        # text.  This prevents an organisation appearing only in Education or
        # Training from passing as an employer.  For free-form CVs (no
        # recognised headings), fall back to full-text matching so we don't
        # break existing behaviour.
        employment_text = _extract_employment_section_text(orig)
        # None → headingless CV: fall back to full text for backward compat.
        # "" → headings found but no employment section: use empty evidence
        #       (employer claims must not pass against non-employment sections).
        # Non-empty → employment section text: use it directly.
        if employment_text is None:
            employer_evidence_text = orig
        else:
            employer_evidence_text = employment_text

        if employer and not _present_in(employer, employer_evidence_text):
            # Verbatim match in the employment section failed.  Apply a
            # token-overlap check against employment-section tokens.
            employer_tokens = _employer_tokens(employer)
            orig_employer_evidence = _extract_employer_tokens_from_cv(orig)
            matching_tokens = employer_tokens & orig_employer_evidence
            # Require at least 2 matching tokens, OR the whole name reduces to a
            # single meaningful token (e.g. a one-word company name).
            min_needed = 1 if len(employer_tokens) == 1 else 2
            if len(matching_tokens) < min_needed:
                return {
                    "section": section,
                    "item_index": item_idx,
                    "claim": f"Employer: {employer!r}",
                    "classification": "unsupported",
                    "detail": (
                        f"Employer '{employer}' not found in original employment history "
                        f"(matched {len(matching_tokens)}/{len(employer_tokens)} "
                        f"meaningful tokens against date-bearing CV lines)."
                    ),
                }
            else:
                # At least two tokens matched but full name didn't — allow as
                # a needs_confirmation warning (possible abbreviation / rewording).
                issues.append(
                    f"Full employer name '{employer}' not in original; "
                    f"{len(matching_tokens)} token(s) matched in employment lines: "
                    f"{matching_tokens}."
                )

        # Title check (soft — rewordings are common)
        if title and not _present_in(title, orig):
            overlap = _overlap_ratio(title, orig)
            if overlap < cls.REWORDING_THRESHOLD:
                issues.append(f"Job title '{title}' has low overlap with original ({overlap:.0%}).")

        # Date check: years in item must be a subset of original years
        if dates:
            item_years = set(_YEAR_RE.findall(dates))
            if item_years and not item_years.issubset(orig_years):
                new_years = item_years - orig_years
                return {
                    "section": section,
                    "item_index": item_idx,
                    "claim": f"Dates: {dates!r}",
                    "classification": "unsupported",
                    "detail": (
                        f"Year(s) {new_years} in generated CV not found in original. "
                        "Possible fabricated date."
                    ),
                }

        # Quantified figures in detail bullets
        all_detail_text = " ".join(
            d for d in (details if isinstance(details, list) else [details])
            if isinstance(d, str)
        )
        quant_findings = cls._validate_quantified_figures(
            all_detail_text, section, orig_quant
        )
        for qf in quant_findings:
            if qf["classification"] in ("unsupported", "contradicted"):
                return {
                    "section": section,
                    "item_index": item_idx,
                    "claim": qf["claim"],
                    "classification": "unsupported",
                    "detail": qf["detail"],
                }
            elif qf["classification"] == "needs_confirmation":
                issues.append(qf["detail"])

        # ── Duration-claim validation ──────────────────────────────────────
        # Flag fabricated experience-duration claims like "3+ years of
        # paid-search experience" that cannot be traced to the original CV.
        duration_claims = _DURATION_CLAIM_RE.findall(all_detail_text)
        if duration_claims:
            # Check if the duration claim's keywords appear in the original
            for claim in duration_claims:
                claim_lower = claim.lower()
                # Extract the domain keyword (e.g. "paid-search" from
                # "3+ years of paid-search experience")
                domain_match = re.search(
                    r"(?:years?|months?|yrs?)\s+of\s+(.+?)\s+(?:experience|expertise|background)",
                    claim_lower,
                )
                domain_keyword = domain_match.group(1) if domain_match else ""
                if domain_keyword and domain_keyword not in orig.lower():
                    issues.append(
                        f"Duration claim '{claim}' references '{domain_keyword}' "
                        "which is not found in the original CV."
                    )

        if issues:
            return {
                "section": section,
                "item_index": item_idx,
                "claim": f"{title or employer}",
                "classification": "needs_confirmation",
                "detail": "; ".join(issues),
            }

        return {
            "section": section,
            "item_index": item_idx,
            "claim": f"{title or employer}",
            "classification": "supported",
            "detail": "Employer, title, and dates traced to original CV.",
        }

    @classmethod
    def _validate_education_item(
        cls,
        item: dict[str, Any],
        item_idx: int,
        section: str,
        orig: str,
        orig_years: set[str],
    ) -> dict[str, Any]:
        institution = (
            item.get("institution")
            or item.get("organisation")
            or item.get("school")
            or item.get("university")
            or ""
        ).strip()
        qualification = (
            item.get("qualification")
            or item.get("degree")
            or item.get("title")
            or item.get("name")
            or ""
        ).strip()
        dates = (item.get("dates") or item.get("date") or "").strip()

        if institution and not _present_in(institution, orig):
            # Use the same token-overlap approach as employer and skill matching.
            # Require min(2, len(inst_tokens)) matching tokens to avoid false
            # passes on common first words like "University" or "National".
            inst_tokens = _tokens(institution)
            orig_tokens = _tokens(orig)
            if not inst_tokens:
                return {
                    "section": section,
                    "item_index": item_idx,
                    "claim": f"Institution: {institution!r}",
                    "classification": "unsupported",
                    "detail": f"Institution '{institution}' not found in original CV.",
                }
            matching = inst_tokens & orig_tokens
            min_needed = min(2, len(inst_tokens))
            if len(matching) < min_needed:
                return {
                    "section": section,
                    "item_index": item_idx,
                    "claim": f"Institution: {institution!r}",
                    "classification": "unsupported",
                    "detail": (
                        f"Institution '{institution}' not found in original CV "
                        f"(matched {len(matching)}/{len(inst_tokens)} meaningful tokens)."
                    ),
                }

        if qualification and not _present_in(qualification, orig):
            overlap = _overlap_ratio(qualification, orig)
            if overlap < cls.REWORDING_THRESHOLD:
                return {
                    "section": section,
                    "item_index": item_idx,
                    "claim": f"Qualification: {qualification!r}",
                    "classification": "unsupported",
                    "detail": (
                        f"Qualification '{qualification}' has very low overlap with original CV "
                        f"({overlap:.0%}). Possible fabrication."
                    ),
                }

        if dates:
            item_years = set(_YEAR_RE.findall(dates))
            if item_years and not item_years.issubset(orig_years):
                new_years = item_years - orig_years
                return {
                    "section": section,
                    "item_index": item_idx,
                    "claim": f"Education dates: {dates!r}",
                    "classification": "unsupported",
                    "detail": f"Year(s) {new_years} not found in original.",
                }

        return {
            "section": section,
            "item_index": item_idx,
            "claim": qualification or institution,
            "classification": "supported",
            "detail": "Institution and qualification traced to original CV.",
        }

    @staticmethod
    def _validate_certification(
        cert_text: str,
        item_idx: int,
        section: str,
        orig: str,
    ) -> dict[str, Any]:
        cert_text = cert_text.strip()
        if not cert_text:
            return {
                "section": section,
                "item_index": item_idx,
                "claim": "",
                "classification": "supported",
                "detail": "Empty certification entry — skipped.",
            }

        # Reject placeholder values
        if _is_placeholder(cert_text):
            return {
                "section": section,
                "item_index": item_idx,
                "claim": cert_text,
                "classification": "unsupported",
                "detail": f"Placeholder value '{cert_text}' — not real candidate data.",
            }

        if _present_in(cert_text, orig):
            return {
                "section": section,
                "item_index": item_idx,
                "claim": cert_text,
                "classification": "supported",
                "detail": "Certification found in original CV.",
            }

        # Check if the cert looks like a real certification (contains cert keywords)
        if _CERT_SIGNALS.search(cert_text):
            overlap = _overlap_ratio(cert_text, orig)
            if overlap < 0.2:
                return {
                    "section": section,
                    "item_index": item_idx,
                    "claim": cert_text,
                    "classification": "needs_confirmation",
                    "detail": (
                        f"Certification '{cert_text}' not found verbatim in original CV. "
                        "Please confirm the candidate holds this certification."
                    ),
                }

        return {
            "section": section,
            "item_index": item_idx,
            "claim": cert_text,
            "classification": "supported",
            "detail": "Certification token overlap acceptable.",
        }

    @staticmethod
    def _validate_skill(
        skill: str,
        item_idx: int,
        section: str,
        orig: str,
    ) -> dict[str, Any]:
        skill = skill.strip()
        if not skill:
            return {
                "section": section,
                "item_index": item_idx,
                "claim": "",
                "classification": "supported",
                "detail": "Empty skill entry — skipped.",
            }

        # Reject placeholder values
        if _is_placeholder(skill):
            return {
                "section": section,
                "item_index": item_idx,
                "claim": skill,
                "classification": "unsupported",
                "detail": f"Placeholder value '{skill}' detected — not real candidate data.",
            }

        if _present_in(skill, orig):
            return {
                "section": section,
                "item_index": item_idx,
                "claim": skill,
                "classification": "supported",
                "detail": "Skill present in original CV.",
            }
        # Require at least 2 meaningful tokens to match for a "reasonable rewording"
        # classification.  The old first-token-only check was too permissive —
        # "Google Ads" would pass if "Google" appeared anywhere in the original
        # (even in "Google Analytics"), and "Microsoft Ads" would pass if
        # "Microsoft" appeared (even in "Microsoft Office").
        skill_tokens = _tokens(skill)
        orig_tokens = _tokens(orig)
        if skill_tokens:
            matching = skill_tokens & orig_tokens
            min_needed = min(2, len(skill_tokens))
            if len(matching) >= min_needed:
                return {
                    "section": section,
                    "item_index": item_idx,
                    "claim": skill,
                    "classification": "reasonable_rewording",
                    "detail": (
                        f"{len(matching)}/{len(skill_tokens)} skill tokens "
                        f"found in original CV."
                    ),
                }
        return {
            "section": section,
            "item_index": item_idx,
            "claim": skill,
            "classification": "needs_confirmation",
            "detail": (
                f"Skill '{skill}' not found in original CV text. "
                "May be a reasonable inference from role context."
            ),
        }

    @staticmethod
    def _validate_quantified_figures(
        text: str,
        section: str,
        orig_quant: set[str],
    ) -> list[dict[str, Any]]:
        """Flag quantified figures in *text* not traceable to original CV.

        Figures that appear verbatim in *orig_quant* are silently accepted.
        Figures absent from the original are classified ``unsupported`` so
        that both the experience-bullet path and the summary/other path treat
        them consistently.  The experience-item caller already escalated
        ``needs_confirmation`` quants to ``unsupported`` at the item level;
        making the classification uniform here removes that inconsistency and
        lets all callers handle the result the same way.
        """
        findings: list[dict[str, Any]] = []
        for figure in _QUANT_RE.findall(text):
            norm = figure.strip().lower()
            if norm not in orig_quant:
                findings.append({
                    "section": section,
                    "claim": f"Quantified figure: {figure!r}",
                    "classification": "unsupported",
                    "detail": (
                        f"The figure '{figure}' does not appear verbatim in the original CV. "
                        "Remove or confirm this claim before exporting."
                    ),
                })
        return findings


__all__ = ["CVValidator", "CVValidationResult"]
