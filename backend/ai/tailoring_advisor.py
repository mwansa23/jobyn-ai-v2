"""AI CV tailoring advisor service.

Produces structured, actionable CV-tailoring recommendations that can feed
directly into Phase 3 (actual CV rewriting and generation). For each relevant
CV section, the advisor identifies what to retain, rewrite, reorganise,
highlight, or request from the candidate.

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
  "section_recommendations": [
    {
      "section": "",
      "action": "retain|rewrite|reorganise|highlight|add",
      "reason": "",
      "evidence": "",
      "jd_reference": "",
      "suggestion": ""
    }
  ],
  "achievements_to_highlight": [
    {
      "achievement": "",
      "evidence": "",
      "jd_reference": "",
      "suggestion": ""
    }
  ],
  "information_to_request": [
    {
      "question": "",
      "reason": "",
      "section": ""
    }
  ],
  "keyword_optimisation": [
    {
      "keyword": "",
      "action": "add|integrate|reposition",
      "note": ""
    }
  ],
  "priority_actions": [],
  "tailoring_summary": ""
}"""

_REQUIRED_FIELDS = (
    "section_recommendations",
    "achievements_to_highlight",
    "information_to_request",
    "keyword_optimisation",
    "priority_actions",
    "tailoring_summary",
)

_LIST_FIELDS = frozenset(
    {
        "section_recommendations",
        "achievements_to_highlight",
        "information_to_request",
        "keyword_optimisation",
        "priority_actions",
    }
)


class TailoringAdvisorError(Exception):
    """Raised when CV tailoring recommendations cannot be generated."""


class TailoringAdvisorService:
    """Generate structured CV-tailoring recommendations from a match result."""

    async def advise(
        self,
        cv_text: str,
        jd_analysis: dict[str, Any],
        match_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Produce structured tailoring recommendations.

        Args:
            cv_text: The plain-text CV content.
            jd_analysis: The structured JD analysis from
                :class:`JDAnalyzerService`.
            match_result: The evidence-based match result from
                :class:`CVJDMatcherService`.

        Returns:
            A dictionary with keys for ``section_recommendations``,
            ``achievements_to_highlight``, ``information_to_request``,
            ``keyword_optimisation``, ``priority_actions``, and
            ``tailoring_summary``.

        Raises:
            TailoringAdvisorError: If any input is empty or the LLM fails.
        """
        if not cv_text or not cv_text.strip():
            raise TailoringAdvisorError("CV text must not be empty.")
        if not jd_analysis or not isinstance(jd_analysis, dict):
            raise TailoringAdvisorError("Job description analysis must be a non-empty dictionary.")
        if not match_result or not isinstance(match_result, dict):
            raise TailoringAdvisorError("Match result must be a non-empty dictionary.")

        prompt = self._build_prompt(cv_text, jd_analysis, match_result)

        try:
            result = await generate_json(prompt, max_tokens=8192)
        except LLMError as exc:
            logger.exception(
                "TailoringAdvisorService.advise failed after LLMError "
                "exception_type=%s exception_message=%s",
                type(exc).__name__,
                str(exc),
            )
            raise TailoringAdvisorError(
                "Failed to generate CV tailoring recommendations."
            ) from exc

        return self._normalize_response(result)

    @staticmethod
    def _build_prompt(
        cv_text: str,
        jd_analysis: dict[str, Any],
        match_result: dict[str, Any],
    ) -> str:
        """Build the prompt that asks the LLM for structured recommendations."""
        serialized_jd = json.dumps(jd_analysis, ensure_ascii=False, indent=2)
        serialized_match = json.dumps(match_result, ensure_ascii=False, indent=2)
        return (
            "You are an expert CV writer and career coach. Based on the CV text, "
            "the job-description analysis, and the evidence-based match result "
            "below, produce structured CV-tailoring recommendations.\n\n"
            "Return ONLY valid JSON matching exactly this schema:\n"
            f"{_OUTPUT_SCHEMA}\n\n"
            "CRITICAL INTEGRITY RULES:\n"
            "- `evidence` fields MUST quote or paraphrase actual text from the CV. "
            "Never invent qualifications, employment history, dates, skills, or "
            "achievements that are not present in the CV.\n"
            "- `action` values: 'retain' (keep as-is), 'rewrite' (change wording), "
            "'reorganise' (move/prioritise), 'highlight' (emphasise), 'add' (new "
            "content needed).\n"
            "- `information_to_request` is for missing information the candidate "
            "should provide — never fabricate it.\n"
            "- `keyword_optimisation` identifies JD keywords that should be added, "
            "integrated, or repositioned in the CV.\n"
            "- `priority_actions` is an ordered list of the most impactful steps.\n"
            "- `tailoring_summary` is a 2-3 sentence summary of the overall plan.\n"
            "- Base everything strictly on the provided CV, JD analysis, and match "
            "result. Do not invent.\n\n"
            "Job description analysis:\n"
            f"{serialized_jd}\n\n"
            "Match result:\n"
            f"{serialized_match}\n\n"
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


__all__ = ["TailoringAdvisorService", "TailoringAdvisorError"]
