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
import re
from copy import deepcopy
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
  "edit_summary": "",
  "section_explanations": [
    {"section_index": 0, "explanation": ""}
  ],
  "needs_clarification": false,
  "clarification_question": ""
}"""

_SECTION_ALIASES = {
    "summary": ("professional summary", "summary", "profile", "objective"),
    "experience": (
        "professional experience",
        "work experience",
        "work history",
        "employment history",
    ),
    "skills": ("technical skills", "core skills", "skills"),
    "education": ("education", "academic background"),
    "certifications": ("certifications", "certificates", "licenses"),
    "projects": ("projects", "selected projects"),
    "contact": ("contact information", "contact details", "contact"),
}


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

        target_index = self._detect_target_section(current_cv, edit_request)
        prompt = self._build_prompt(
            current_cv, edit_request, original_cv_text, target_index
        )

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

        normalized = self._normalize_response(result)
        if normalized["needs_clarification"]:
            if not normalized["clarification_question"].strip():
                raise CVEditorError(
                    "The editor requested clarification without providing a question."
                )
            return normalized

        if target_index is not None:
            current_sections = current_cv.get("sections")
            edited_sections = normalized["sections"]
            if (
                not isinstance(current_sections, list)
                or len(edited_sections) != len(current_sections)
                or not isinstance(edited_sections[target_index], dict)
            ):
                raise CVEditorError(
                    "The section-specific edit did not return the complete CV structure."
                )
            sections = deepcopy(current_sections)
            sections[target_index] = edited_sections[target_index]
            normalized["sections"] = sections
        return normalized

    @staticmethod
    def _detect_target_section(
        current_cv: dict[str, Any],
        edit_request: str,
    ) -> int | None:
        """Return the sole clearly named section, otherwise preserve whole-CV mode."""
        sections = current_cv.get("sections")
        if not isinstance(sections, list):
            return None

        request = edit_request.casefold()
        matches: set[int] = set()
        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            names = {
                str(section.get("heading") or "").casefold().strip(),
                str(section.get("type") or "").casefold().strip(),
            }
            for name in tuple(names):
                names.update(_SECTION_ALIASES.get(name, ()))
            for name in names:
                if not name or name == "other":
                    continue
                # "Experience" alone often describes a candidate's history,
                # not an explicit request to edit the Experience section.
                if name == "experience":
                    pattern = r"\b(?:experience\s+section|section\s*:\s*experience)\b"
                else:
                    pattern = rf"\b{re.escape(name)}\b"
                if re.search(pattern, request):
                    matches.add(index)
                    break
        return next(iter(matches)) if len(matches) == 1 else None

    @staticmethod
    def _build_prompt(
        current_cv: dict[str, Any],
        edit_request: str,
        original_cv_text: str,
        target_index: int | None,
    ) -> str:
        """Build the prompt for applying a conversational edit."""
        cv_for_edit = {
            key: value for key, value in current_cv.items() if key != "comparison"
        }
        serialized_cv = json.dumps(cv_for_edit, ensure_ascii=False, indent=2)
        original_ref = (
            f"\n\nOriginal CV text (for reference — do not fabricate beyond this):\n"
            f"{original_cv_text.strip()}"
            if original_cv_text and original_cv_text.strip()
            else ""
        )
        target_instruction = ""
        if target_index is not None:
            target = current_cv["sections"][target_index]
            target_instruction = (
                f"\n\nThis is a section-specific request. Only section index "
                f"{target_index} ({target.get('heading') or target.get('type')}) "
                "may be edited. Return all sections in their original order, "
                "leaving every other section byte-for-byte unchanged.\n"
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
            "- If a requested change depends on essential factual information "
            "that is not present in the current or original CV, do not change "
            "the CV. Set ``needs_clarification`` to true and ask one concise "
            "question in ``clarification_question``. Do not guess or fabricate.\n"
            "- Set ``source`` to 'candidate_confirmed' for any section that "
            "incorporates candidate-provided information from the edit request.\n"
            "- Return the COMPLETE CV with all sections, not just the "
            "changed section.\n"
            "- ``edit_summary`` should briefly describe what was changed.\n\n"
            "- Return ``section_explanations`` with one concise explanation "
            "for each changed section, using its zero-based index in the "
            "returned sections list. Explain how the change follows the "
            "candidate's request and available CV evidence. Do not claim "
            "ATS compatibility or improved job suitability unless supported "
            "by explicit evidence in the request or supplied CV content.\n"
            f"{target_instruction}"
            "Current tailored CV:\n"
            f"{serialized_cv}\n\n"
            f"Candidate edit request: {edit_request.strip()}"
            f"{original_ref}"
        )

    @staticmethod
    def _normalize_response(result: dict[str, Any]) -> dict[str, Any]:
        """Ensure every expected key is present with the correct type."""
        normalized = dict(result)
        normalized.setdefault("sections", [])
        normalized.setdefault("edit_summary", "")
        normalized.setdefault("section_explanations", [])
        normalized.setdefault("needs_clarification", False)
        normalized.setdefault("clarification_question", "")
        if not isinstance(normalized["needs_clarification"], bool):
            raise CVEditorError("The editor returned an invalid clarification flag.")
        if not isinstance(normalized["clarification_question"], str):
            raise CVEditorError("The editor returned an invalid clarification question.")
        if not isinstance(normalized["section_explanations"], list):
            raise CVEditorError("The editor returned invalid section explanations.")
        for explanation in normalized["section_explanations"]:
            if (
                not isinstance(explanation, dict)
                or not isinstance(explanation.get("section_index"), int)
                or not isinstance(explanation.get("explanation"), str)
            ):
                raise CVEditorError("The editor returned an invalid section explanation.")
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
