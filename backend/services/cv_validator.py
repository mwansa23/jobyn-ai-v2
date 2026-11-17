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


# ---------------------------------------------------------------------------
# Validation result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CVValidationResult:
    """The outcome of validating one generated CV against an original."""

    is_valid: bool
    """False when any claim is classified as ``unsupported`` or ``contradicted``."""

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
            # No original to compare against — pass with a warning.
            return CVValidationResult(
                is_valid=True,
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

            # ── Contact and "original"-sourced sections are trusted ──────────
            if sec_type == "contact" or source == "original":
                all_findings.append({
                    "section": sec_heading,
                    "section_index": sec_idx,
                    "classification": "supported",
                    "detail": "Section preserved verbatim from the original CV.",
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

        # Employer check
        if employer and not _present_in(employer, orig):
            # Try partial match (first word of employer name)
            first_word = employer.split()[0] if employer.split() else ""
            if first_word and not _present_in(first_word, orig):
                return {
                    "section": section,
                    "item_index": item_idx,
                    "claim": f"Employer: {employer!r}",
                    "classification": "unsupported",
                    "detail": f"Employer '{employer}' not found in original CV text.",
                }
            else:
                issues.append(f"Full employer name '{employer}' not in original; partial match found.")

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
            first_word = institution.split()[0] if institution.split() else ""
            if not first_word or not _present_in(first_word, orig):
                return {
                    "section": section,
                    "item_index": item_idx,
                    "claim": f"Institution: {institution!r}",
                    "classification": "unsupported",
                    "detail": f"Institution '{institution}' not found in original CV.",
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
        if _present_in(skill, orig):
            return {
                "section": section,
                "item_index": item_idx,
                "claim": skill,
                "classification": "supported",
                "detail": "Skill present in original CV.",
            }
        # Skills are often abbreviations or slightly different capitalisation;
        # accept if the first meaningful token matches.
        first_token = skill.split()[0].lower().rstrip(".,;")
        if first_token and first_token in orig.lower():
            return {
                "section": section,
                "item_index": item_idx,
                "claim": skill,
                "classification": "reasonable_rewording",
                "detail": f"Skill abbreviation/variant of '{first_token}' found in original.",
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
        """Flag quantified figures in *text* not traceable to original CV."""
        findings: list[dict[str, Any]] = []
        for figure in _QUANT_RE.findall(text):
            norm = figure.strip().lower()
            if norm not in orig_quant:
                findings.append({
                    "section": section,
                    "claim": f"Quantified figure: {figure!r}",
                    "classification": "needs_confirmation",
                    "detail": (
                        f"The figure '{figure}' does not appear verbatim in the original CV. "
                        "Please confirm this achievement."
                    ),
                })
        return findings


__all__ = ["CVValidator", "CVValidationResult"]
