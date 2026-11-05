"""AI conversational CV editing service.

Applies a candidate's natural-language edit request to the current tailored
CV and returns the updated version. The edit is applied to the actual CV
content, not merely explained.

CRITICAL INTEGRITY RULES:
- Never invent information not present in the original or current CV.
- Only rewrite, reorganise, or improve existing content.
- Candidate-confirmed additions must be marked as ``source: "candidate_confirmed"``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.ai.llm import LLMError, generate_json

logger = logging.getLogger(__name__)

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
  "edit_summary": ""
}"""

_REQUIRED_FIELDS = (
    "sections",
    "edit_summary",
)


class CVEditorError(Exception):
    """Raised when a CV edit cannot be applied."""


class CVEditorService:
    """Apply conversational edit requests to a tailored CV."""

    async def apply_edit(
        self,
        current_cv: dict[str, Any],
        edit_request: str,
        original_cv_text: str = "",
    ) -> dict[str, Any]:
        """Apply a natural-language edit to the current tailored CV.

        Args:
            current_cv: The current tailored CV (sections list + metadata).
            edit_request: The candidate's natural-language edit request.
            original_cv_text: The original CV text (for integrity reference).

        Returns:
            Updated CV dictionary with ``sections`` and ``edit_summary``.

        Raises:
            CVEditorError: If inputs are empty or the LLM fails.
        """
        if not current_cv or not isinstance(current_cv, dict):
            raise CVEditorError("Current CV must be a non-empty dictionary.")
        if not edit_request or not edit_request.strip():
            raise CVEditorError("Edit request must not be empty.")

        prompt = self._build_prompt(current_cv, edit_request, original_cv_text)

        try:
            result = await generate_json(prompt, max_tokens=8192)
        except LLMError as exc:
            logger.exception(
                "CVEditorService.apply_edit failed after LLMError "
                "exception_type=%s exception_message=%s",
                type(exc).__name__,
                str(exc),
            )
            raise CVEditorError("Failed to apply the edit to the CV.") from exc

        return self._normalize_response(result)

    @staticmethod
    def _build_prompt(
        current_cv: dict[str, Any],
        edit_request: str,
        original_cv_text: str,
    ) -> str:
        """Build the prompt for applying a conversational edit."""
        serialized_cv = json.dumps(current_cv, ensure_ascii=False, indent=2)
        original_ref = (
            f"\n\nOriginal CV text (for reference — do not fabricate beyond this):\n"
            f"{original_cv_text.strip()}"
            if original_cv_text and original_cv_text.strip()
            else ""
        )
        return (
            "You are an expert professional CV editor. The candidate has "
            "requested a change to their tailored CV. Apply the change to "
            "the actual CV content and return the COMPLETE updated CV.\n\n"
            "Return ONLY valid JSON matching exactly this schema:\n"
            f"{_OUTPUT_SCHEMA}\n\n"
            "CRITICAL INTEGRITY RULES:\n"
            "- NEVER invent qualifications, employers, dates, skills, or "
            "achievements not present in the current CV or original CV.\n"
            "- Only rewrite, reorganise, or improve existing content.\n"
            "- If the edit request asks for something that requires new "
            "information the candidate has not provided, note this in "
            "``edit_summary`` and make the best improvement possible with "
            "existing content.\n"
            "- Set ``source`` to 'candidate_confirmed' for any section that "
            "incorporates candidate-provided information from the edit request.\n"
            "- Return the COMPLETE CV with all sections, not just the "
            "changed section.\n"
            "- ``edit_summary`` should briefly describe what was changed.\n\n"
            "Current tailored CV:\n"
            f"{serialized_cv}\n\n"
            f"Candidate edit request: {edit_request.strip()}"
            f"{original_ref}"
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


__all__ = ["CVEditorService", "CVEditorError"]
