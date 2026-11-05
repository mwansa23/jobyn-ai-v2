"""AI job-description analysis service.

Extracts and structures the key components of a job description so that
CV-to-JD matching and tailoring recommendations can operate on structured data
rather than raw text.

This module contains only AI analysis orchestration — no framework, database,
or API concerns. It follows the same pattern as ``career_navigator.py`` and
``cv_analyzer.py``: build a prompt, call ``generate_json``, normalise the
result.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.ai.llm import LLMError, generate_json

logger = logging.getLogger(__name__)

# ── Output schema ────────────────────────────────────────────────────────────

_OUTPUT_SCHEMA = """{
  "job_title": "",
  "employer": "",
  "location": "",
  "employment_type": "",
  "responsibilities": [],
  "essential_qualifications": [],
  "desirable_qualifications": [],
  "required_experience_years": "",
  "technical_skills": [],
  "soft_skills": [],
  "industry_keywords": [],
  "application_requirements": [],
  "summary": ""
}"""

_REQUIRED_FIELDS = (
    "job_title",
    "employer",
    "location",
    "employment_type",
    "responsibilities",
    "essential_qualifications",
    "desirable_qualifications",
    "required_experience_years",
    "technical_skills",
    "soft_skills",
    "industry_keywords",
    "application_requirements",
    "summary",
)

_LIST_FIELDS = frozenset(
    {
        "responsibilities",
        "essential_qualifications",
        "desirable_qualifications",
        "technical_skills",
        "soft_skills",
        "industry_keywords",
        "application_requirements",
    }
)


class JDAnalysisError(Exception):
    """Raised when a job description cannot be analysed."""


class JDAnalyzerService:
    """Extract structured information from a raw job-description text."""

    async def analyze(self, jd_text: str) -> dict[str, Any]:
        """Analyse a job description and return a structured breakdown.

        Args:
            jd_text: The plain-text job description (pasted or extracted).

        Returns:
            A dictionary with keys for ``job_title``, ``employer``,
            ``location``, ``employment_type``, ``responsibilities``,
            ``essential_qualifications``, ``desirable_qualifications``,
            ``required_experience_years``, ``technical_skills``,
            ``soft_skills``, ``industry_keywords``,
            ``application_requirements``, and ``summary``.

        Raises:
            JDAnalysisError: If the text is empty or the LLM fails.
        """
        if not jd_text or not jd_text.strip():
            raise JDAnalysisError("Job description text must not be empty.")

        prompt = self._build_prompt(jd_text)

        try:
            result = await generate_json(prompt)
        except LLMError as exc:
            logger.exception(
                "JDAnalyzerService.analyze failed after LLMError "
                "exception_type=%s exception_message=%s",
                type(exc).__name__,
                str(exc),
            )
            raise JDAnalysisError("Failed to analyse the job description.") from exc

        return self._normalize_response(result)

    @staticmethod
    def _build_prompt(jd_text: str) -> str:
        """Build the prompt that asks the LLM for a structured JD breakdown."""
        return (
            "You are an expert recruitment analyst. Analyse the job description "
            "below and extract its key components.\n\n"
            "Return ONLY valid JSON matching exactly this schema:\n"
            f"{_OUTPUT_SCHEMA}\n\n"
            "Guidelines:\n"
            "- `job_title`: the exact job title stated in the description.\n"
            "- `employer`: the company or organisation name if present.\n"
            "- `location`: the work location (city, country, or 'Remote').\n"
            "- `employment_type`: full-time, part-time, contract, etc.\n"
            "- `responsibilities`: a list of duty/responsibility statements.\n"
            "- `essential_qualifications`: must-have requirements.\n"
            "- `desirable_qualifications`: nice-to-have or preferred qualifications.\n"
            "- `required_experience_years`: e.g. '5 years' or '3-5 years'.\n"
            "- `technical_skills`: specific tools, languages, frameworks, platforms.\n"
            "- `soft_skills`: communication, leadership, teamwork, etc.\n"
            "- `industry_keywords`: domain-specific terms and buzzwords.\n"
            "- `application_requirements`: documents, portfolios, references, etc.\n"
            "- `summary`: a 2-3 sentence summary of the role.\n"
            "- Use empty strings and empty lists when information is not present.\n"
            "- Base everything strictly on the provided text. Do not invent.\n\n"
            "Job description text:\n"
            f"{jd_text.strip()}"
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


__all__ = ["JDAnalyzerService", "JDAnalysisError"]
