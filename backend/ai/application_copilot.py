"""
AI Application Copilot.

This module contains the AI-layer implementation responsible for generating
structured job-application content.

The service layer is responsible for:
    - loading the job
    - loading the user's resume
    - loading career insights
    - enforcing ownership

This module is responsible only for AI generation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.ai.tabitoken_client import TaBiTokenClient

logger = logging.getLogger(__name__)


class ApplicationCopilotError(Exception):
    """Raised when application copilot generation fails."""

def normalize_application_analysis(
    analysis: dict[str, Any],
) -> dict[str, Any]:
    """
    Normalize AI-generated application analysis into frontend-safe strings.

    The LLM may return either strings or structured dictionaries for
    list-based fields. This helper converts both forms into consistent
    string lists.
    """

    list_fields = [
        "key_selling_points",
        "matched_requirements",
        "addressed_skill_gaps",
        "application_tips",
    ]

    normalized = dict(analysis)

    field_text_keys = {
        "key_selling_points": ("point", "evidence"),
        "matched_requirements": ("requirement", "evidence"),
        "addressed_skill_gaps": ("gap", "recommendation"),
        "application_tips": ("tip", "reason"),
    }

    for field in list_fields:
        value = normalized.get(field, [])

        if not isinstance(value, list):
            value = [value]

        result: list[str] = []

        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    result.append(text)

            elif isinstance(item, dict):
                keys = field_text_keys[field]

                parts = []

                for key in keys:
                    value_part = item.get(key)

                    if value_part is not None:
                        text_part = str(value_part).strip()

                        if text_part:
                            parts.append(text_part)

                if parts:
                    result.append(" — ".join(parts))

            elif item is not None:
                result.append(str(item).strip())

        normalized[field] = result

    return normalized
class ApplicationCopilotService:
    """
    AI service for generating a complete job application package.

    This class deliberately has no database/repository dependencies.
    """

    def __init__(self, cover_letter_client: TaBiTokenClient | None = None) -> None:
        self._cover_letter_client = cover_letter_client

    async def generate(
        self,
        *,
        candidate_profile: dict[str, Any],
        job_details: dict[str, Any],
        career_context: dict[str, Any] | None = None,
        additional_context: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate the structured application package.

        Cover-letter generation uses the configured TaBiAI/Claude provider.
        """

        try:
            return await self._generate_with_llm(
                candidate_profile=candidate_profile,
                job_details=job_details,
                career_context=career_context,
                additional_context=additional_context,
            )

        except ApplicationCopilotError:
            raise

        except Exception as exc:
            logger.exception("Application copilot generation failed")
            raise ApplicationCopilotError(
                f"Application copilot generation failed: {exc}"
            ) from exc

    async def _generate_with_llm(
        self,
        *,
        candidate_profile: dict[str, Any],
        job_details: dict[str, Any],
        career_context: dict[str, Any] | None,
        additional_context: str | None,
    ) -> dict[str, Any]:
        """
        Generate application content.

        IMPORTANT:
        Keep the actual LLM integration here.

        The returned dictionary must contain these fields:

            cover_letter
            key_selling_points
            matched_requirements
            addressed_skill_gaps
            application_tips
        """

        job_title = job_details.get("title") or "the position"
        company = job_details.get("company") or "your organisation"

        skills = candidate_profile.get("skills") or []
        technical_skills = candidate_profile.get("technical_skills") or []

        all_skills: list[str] = []

        for skill in [*skills, *technical_skills]:
            if isinstance(skill, str) and skill.strip():
                if skill.strip() not in all_skills:
                    all_skills.append(skill.strip())

        job_description = str(job_details.get("description") or "").strip()
        matched_requirements = [
            skill
            for skill in all_skills
            if skill.casefold() in job_description.casefold()
        ][:8]
        career_strengths = career_context or {}
        raw_strengths = career_strengths.get("strengths", [])
        if not isinstance(raw_strengths, list):
            raw_strengths = []
        key_selling_points = [
            str(point).strip()
            for point in raw_strengths
            if isinstance(point, str) and point.strip()
        ][:8]
        key_selling_points.extend(
            skill for skill in all_skills if skill not in key_selling_points
        )
        key_selling_points = key_selling_points[:8]

        cover_letter_client = self._cover_letter_client or TaBiTokenClient()
        cover_letter = await cover_letter_client.generate_cover_letter(
            resume=json.dumps(candidate_profile, ensure_ascii=False, indent=2),
            job_title=job_title,
            company=company,
            job_description=job_description,
            key_selling_points=key_selling_points,
            matched_requirements=matched_requirements,
            addressed_skill_gaps=[],
            additional_context=additional_context,
        )
        if not cover_letter.strip():
            raise ApplicationCopilotError(
                "The cover-letter provider returned an empty letter."
            )

        return {
            "cover_letter": cover_letter,
            "key_selling_points": [
                *key_selling_points,
            ],
            "matched_requirements": [
                {
                    "requirement": skill,
                    "evidence": (
                        f"{skill} appears in both the candidate profile and "
                        "the job description."
                    ),
                }
                for skill in matched_requirements[:3]
            ],
            "addressed_skill_gaps": [],
            "application_tips": [
                "Tailor the cover letter to the specific job description.",
                "Highlight measurable achievements from your experience.",
                "Prepare examples that demonstrate your strongest technical skills.",
            ],
        }


__all__ = [
    "ApplicationCopilotError",
    "ApplicationCopilotService",
    "normalize_application_analysis",

]