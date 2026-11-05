"""AI CV rewriting service.

Transforms a candidate's original CV into a professionally tailored CV using
the Phase 2 analysis (JD analysis, match results, and recommendations).

This module contains only AI analysis orchestration — no framework, database,
or API concerns. It follows the same pattern as ``career_navigator.py``:
build a prompt, call ``generate_json``, normalise the result.

CRITICAL INTEGRITY RULES:
- Never invent qualifications, employers, dates, skills, or achievements.
- Only rewrite and reorganise information present in the original CV.
- Mark any candidate-confirmed additions with ``source: "candidate_confirmed"``.
- Preserve original employment history and education verbatim (only improve wording).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.ai.llm import LLMError, generate_json

logger = logging.getLogger(__name__)

# ── Output schema ────────────────────────────────────────────────────────────

_OUTPUT_SCHEMA = """{
  "sections": [
    {
      "heading": "",
      "type": "contact|summary|experience|education|skills|certifications|projects|other",
      "content": "",
      "items": [],
      "source": "original|rewritten|candidate_confirmed"
    }
  ],
  "generation_notes": ""
}"""

_REQUIRED_FIELDS = (
    "sections",
    "generation_notes",
)


class CVRewriterError(Exception):
    """Raised when the CV cannot be rewritten."""


class CVRewriterService:
    """Rewrite a candidate's CV into a tailored version using Phase 2 analysis."""

    async def rewrite(
        self,
        cv_text: str,
        jd_analysis: dict[str, Any],
        match_result: dict[str, Any],
        recommendations: dict[str, Any],
    ) -> dict[str, Any]:
        """Produce a complete, rewritten CV tailored to the job description.

        Args:
            cv_text: The plain-text original CV content.
            jd_analysis: Structured JD analysis from :class:`JDAnalyzerService`.
            match_result: Evidence-based match from :class:`CVJDMatcherService`.
            recommendations: Tailoring recommendations from
                :class:`TailoringAdvisorService`.

        Returns:
            A dictionary with ``sections`` (list of section dicts, each with
            ``heading``, ``type``, ``content``, ``items``, and ``source``)
            and ``generation_notes`` (string).

        Raises:
            CVRewriterError: If any input is empty or the LLM fails.
        """
        if not cv_text or not cv_text.strip():
            raise CVRewriterError("CV text must not be empty.")
        if not jd_analysis or not isinstance(jd_analysis, dict):
            raise CVRewriterError("Job description analysis must be a non-empty dictionary.")

        prompt = self._build_prompt(cv_text, jd_analysis, match_result, recommendations)

        try:
            result = await generate_json(prompt, max_tokens=8192)
        except LLMError as exc:
            logger.exception(
                "CVRewriterService.rewrite failed after LLMError "
                "exception_type=%s exception_message=%s",
                type(exc).__name__,
                str(exc),
            )
            raise CVRewriterError("Failed to rewrite the CV.") from exc

        return self._normalize_response(result)

    @staticmethod
    def _build_prompt(
        cv_text: str,
        jd_analysis: dict[str, Any],
        match_result: dict[str, Any],
        recommendations: dict[str, Any],
    ) -> str:
        """Build the prompt that asks the LLM for a complete rewritten CV."""
        serialized_jd = json.dumps(jd_analysis, ensure_ascii=False, indent=2)
        serialized_match = json.dumps(match_result, ensure_ascii=False, indent=2)
        serialized_recs = json.dumps(recommendations, ensure_ascii=False, indent=2)
        return (
            "You are an expert professional CV writer. Using the candidate's "
            "original CV, the job-description analysis, the evidence-based "
            "match result, and the tailoring recommendations below, produce a "
            "COMPLETE rewritten CV tailored to the target position.\n\n"
            "Return ONLY valid JSON matching exactly this schema:\n"
            f"{_OUTPUT_SCHEMA}\n\n"
            "CRITICAL INTEGRITY RULES — VIOLATION IS UNACCEPTABLE:\n"
            "- NEVER invent qualifications, certifications, employers, job "
            "titles, employment dates, responsibilities, achievements, "
            "performance figures, or technical skills that are not present in "
            "the original CV.\n"
            "- ONLY rewrite, reorganise, and improve the wording of information "
            "that IS present in the original CV.\n"
            "- You MAY improve clarity, grammar, and professional language.\n"
            "- You MAY reorganise sections to prioritise relevant experience.\n"
            "- You MAY integrate JD keywords that the candidate demonstrably "
            "possesses (based on the match result).\n"
            "- You MAY NOT add keywords from the JD that the candidate does "
            "not have evidence for in their CV.\n"
            "- For each section, set ``source`` to 'original' (kept as-is), "
            "'rewritten' (wording changed), or 'candidate_confirmed' (added "
            "from candidate-provided information — only if explicitly noted "
            "in the recommendations).\n"
            "- The rewritten CV must be COMPLETE — include all sections from "
            "the original CV, rewritten and reorganised as needed.\n"
            "- ``content`` is for single-paragraph sections (summary). "
            "``items`` is for list-based sections (experience, skills, "
            "education, certifications). Use one or the other per section.\n"
            "- ``generation_notes`` should briefly explain what was changed "
            "and what was preserved.\n\n"
            "Section types:\n"
            "- 'contact': Name and contact details (preserve verbatim)\n"
            "- 'summary': Professional summary/profile (rewrite for target role)\n"
            "- 'experience': Work history entries (improve wording, prioritise)\n"
            "- 'education': Education (preserve verbatim)\n"
            "- 'skills': Skills section (reorganise, add only verified keywords)\n"
            "- 'certifications': Certifications (preserve, add only if confirmed)\n"
            "- 'projects': Projects (improve wording if present)\n"
            "- 'other': Any other relevant section\n\n"
            "Job description analysis:\n"
            f"{serialized_jd}\n\n"
            "Match result:\n"
            f"{serialized_match}\n\n"
            "Tailoring recommendations:\n"
            f"{serialized_recs}\n\n"
            "Original CV text:\n"
            f"{cv_text.strip()}"
        )

    @staticmethod
    def _normalize_response(result: dict[str, Any]) -> dict[str, Any]:
        """Ensure every expected key is present with the correct type."""
        normalized = dict(result)
        for field in _REQUIRED_FIELDS:
            value = normalized.get(field)
            if value is None:
                normalized[field] = [] if field == "sections" else ""
        if not isinstance(normalized.get("sections"), list):
            normalized["sections"] = []
        # Ensure each section has required keys
        for section in normalized["sections"]:
            if not isinstance(section, dict):
                continue
            section.setdefault("heading", "")
            section.setdefault("type", "other")
            section.setdefault("content", "")
            section.setdefault("items", [])
            section.setdefault("source", "rewritten")
            if not isinstance(section.get("items"), list):
                section["items"] = []
        return normalized


__all__ = ["CVRewriterService", "CVRewriterError"]
