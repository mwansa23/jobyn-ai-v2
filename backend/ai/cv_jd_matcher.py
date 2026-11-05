"""AI CV-to-JD matching service.

Compares a candidate's CV text against a structured job-description analysis
and returns evidence-based findings. Every finding must reference actual text
from the CV — never fabricated.

This module contains only AI analysis orchestration — no framework, database,
or API concerns.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.ai.llm import LLMError, generate_json

logger = logging.getLogger(__name__)

# ── Output schema ────────────────────────────────────────────────────────────

_OUTPUT_SCHEMA = """{
  "matching_skills": [
    {
      "skill": "",
      "evidence": "",
      "jd_reference": ""
    }
  ],
  "matching_experience": [
    {
      "requirement": "",
      "evidence": "",
      "strength": "strong|moderate|weak"
    }
  ],
  "matching_achievements": [
    {
      "achievement": "",
      "evidence": "",
      "relevance": ""
    }
  ],
  "missing_requirements": [
    {
      "requirement": "",
      "type": "essential|desirable",
      "note": ""
    }
  ],
  "missing_keywords": [],
  "weak_sections": [
    {
      "section": "",
      "issue": "",
      "evidence": ""
    }
  ],
  "overall_match_summary": ""
}"""

_REQUIRED_FIELDS = (
    "matching_skills",
    "matching_experience",
    "matching_achievements",
    "missing_requirements",
    "missing_keywords",
    "weak_sections",
    "overall_match_summary",
)

_LIST_FIELDS = frozenset(
    {
        "matching_skills",
        "matching_experience",
        "matching_achievements",
        "missing_requirements",
        "missing_keywords",
        "weak_sections",
    }
)


class CVJDMatcherError(Exception):
    """Raised when CV-to-JD matching cannot be performed."""


class CVJDMatcherService:
    """Compare a candidate's CV against a structured job-description analysis."""

    async def match(
        self,
        cv_text: str,
        jd_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Produce an evidence-based comparison of the CV against the JD.

        Args:
            cv_text: The plain-text CV content extracted from the uploaded file.
            jd_analysis: The structured JD analysis from
                :class:`JDAnalyzerService`.

        Returns:
            A dictionary with keys for ``matching_skills``,
            ``matching_experience``, ``matching_achievements``,
            ``missing_requirements``, ``missing_keywords``,
            ``weak_sections``, and ``overall_match_summary``.

        Raises:
            CVJDMatcherError: If either input is empty or the LLM fails.
        """
        if not cv_text or not cv_text.strip():
            raise CVJDMatcherError("CV text must not be empty.")
        if not jd_analysis or not isinstance(jd_analysis, dict):
            raise CVJDMatcherError("Job description analysis must be a non-empty dictionary.")

        prompt = self._build_prompt(cv_text, jd_analysis)

        try:
            result = await generate_json(prompt, max_tokens=8192)
        except LLMError as exc:
            logger.exception(
                "CVJDMatcherService.match failed after LLMError "
                "exception_type=%s exception_message=%s",
                type(exc).__name__,
                str(exc),
            )
            raise CVJDMatcherError("Failed to match the CV against the job description.") from exc

        return self._normalize_response(result)

    @staticmethod
    def _build_prompt(cv_text: str, jd_analysis: dict[str, Any]) -> str:
        """Build the prompt that asks the LLM for evidence-based matching."""
        serialized_jd = json.dumps(jd_analysis, ensure_ascii=False, indent=2)
        return (
            "You are an expert career coach and recruitment analyst. Compare the "
            "candidate's CV text below against the structured job-description "
            "analysis provided.\n\n"
            "Return ONLY valid JSON matching exactly this schema:\n"
            f"{_OUTPUT_SCHEMA}\n\n"
            "CRITICAL INTEGRITY RULES:\n"
            "- `evidence` fields MUST quote or paraphrase actual text from the CV. "
            "Never invent qualifications, experience, or achievements that are not "
            "present in the CV text.\n"
            "- If a requirement from the JD has no corresponding evidence in the CV, "
            "list it under `missing_requirements` instead of fabricating a match.\n"
            "- `strength` is 'strong' when the CV clearly demonstrates the requirement, "
            "'moderate' when partially demonstrated, 'weak' when barely mentioned.\n"
            "- `missing_keywords` are JD keywords (technical_skills, industry_keywords) "
            "that do not appear anywhere in the CV.\n"
            "- `weak_sections` identifies CV sections that need improvement — each must "
            "include `evidence` quoting the actual CV text.\n"
            "- Base everything strictly on the provided CV text and JD analysis.\n\n"
            "Field descriptions:\n"
            "- `matching_skills`: skills present in BOTH the CV and the JD.\n"
            "- `matching_experience`: JD requirements for which the CV shows relevant experience.\n"
            "- `matching_achievements`: candidate achievements relevant to the role.\n"
            "- `missing_requirements`: JD requirements not evidenced in the CV.\n"
            "- `missing_keywords`: JD keywords absent from the CV.\n"
            "- `weak_sections`: CV sections that need strengthening.\n"
            "- `overall_match_summary`: a 2-3 sentence honest summary.\n\n"
            "Job description analysis:\n"
            f"{serialized_jd}\n\n"
            "Candidate CV text:\n"
            f"{cv_text.strip()}"
        )

    @staticmethod
    def _normalize_response(result: dict[str, Any]) -> dict[str, Any]:
        """Ensure every expected key is present with the correct type."""
        normalized = dict(result)
        for field in _REQUIRED_FIELDS:
            value = normalized.get(field)
            if value is None:
                normalized[field] = [] if field in _LIST_FIELDS else ""
        return normalized


__all__ = ["CVJDMatcherService", "CVJDMatcherError"]
