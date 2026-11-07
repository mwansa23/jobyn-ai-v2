"""Pydantic schemas for the CV Tailoring Studio endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TailoringDocumentRead(BaseModel):
    """Public representation of a tailoring document.

    Raw file bytes are never exposed — only metadata and extracted text.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    document_type: str
    source_type: str
    original_filename: str | None = None
    content_type: str | None = None
    file_size_bytes: int | None = None
    extraction_status: str
    extraction_error: str | None = None
    extracted_text: str | None = None
    pasted_text: str | None = None
    created_at: datetime


class TailoringDocumentListResponse(BaseModel):
    """Paginated list of tailoring documents."""

    items: list[TailoringDocumentRead]
    total: int
    offset: int
    limit: int


class JobDescriptionPasteRequest(BaseModel):
    """Request body for pasting a job description as text."""

    text: str = Field(min_length=1, max_length=50000, description="Job description text")


# ── Phase 2: Analysis, matching, and recommendations ─────────────────────────


class TailoringAnalysisRead(BaseModel):
    """Public representation of a tailoring analysis result.

    The ``jd_analysis``, ``match_result``, and ``recommendations`` fields
    contain the full structured JSON output from each pipeline stage.
    CV and JD document IDs link back to the source documents.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    cv_document_id: uuid.UUID
    jd_document_id: uuid.UUID
    jd_analysis: dict[str, Any] | None = None
    match_result: dict[str, Any] | None = None
    recommendations: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime


class TailoringAnalysisListResponse(BaseModel):
    """Paginated list of tailoring analyses."""

    items: list[TailoringAnalysisRead]
    total: int
    offset: int
    limit: int


class AnalyzeRequest(BaseModel):
    """Request body for triggering a CV-to-JD analysis.

    If ``cv_document_id`` or ``jd_document_id`` are omitted, the user's
    latest document of that type is used automatically.
    """

    cv_document_id: uuid.UUID | None = None
    jd_document_id: uuid.UUID | None = None


class ChatRequest(BaseModel):
    """Request body for the conversational tailoring chat endpoint.

    The candidate sends a natural-language message; Jobyn responds with
    guidance grounded in the saved CV and JD analysis.
    """

    message: str = Field(min_length=1, max_length=5000, description="User's chat message")


class ChatResponse(BaseModel):
    """Response from the conversational tailoring chat endpoint."""

    response: str = Field(description="Jobyn's response to the candidate")
    analysis_available: bool = Field(
        description="Whether a completed analysis exists to ground the response"
    )


# ── Phase 3: Tailored CV generation, editing, and export ─────────────────────


class TailoredCVSection(BaseModel):
    """A single section of a tailored CV."""

    heading: str = ""
    type: str = "other"
    content: str = ""
    items: list[Any] = []
    source: str = "rewritten"


class TailoredCVRead(BaseModel):
    """Public representation of a tailored CV version."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    analysis_id: uuid.UUID | None = None
    version_number: int
    status: str
    content: dict[str, Any]
    edit_type: str
    edit_request: str | None = None
    change_summary: str | None = None
    created_at: datetime


class TailoredCVListResponse(BaseModel):
    """Paginated list of tailored CV versions."""

    items: list[TailoredCVRead]
    total: int
    offset: int
    limit: int


class GenerateCVRequest(BaseModel):
    """Request body for generating a tailored CV from the latest analysis."""

    pass  # No parameters — uses the user's latest analysis


class EditCVRequest(BaseModel):
    """Request body for applying a conversational edit to the tailored CV."""

    message: str = Field(min_length=1, max_length=5000, description="Edit request")


class EditCVClarification(BaseModel):
    """Clarification requested before creating a new CV version."""

    needs_clarification: Literal[True] = True
    clarification_question: str


class SetCVStatusRequest(BaseModel):
    """Request body for accepting or rejecting a CV version."""

    status: str = Field(description="'accepted' or 'rejected'")


class RevertCVRequest(BaseModel):
    """Request body for reverting to an earlier version."""

    version_number: int = Field(ge=1, description="Version to revert to")


__all__ = [
    "TailoringDocumentRead",
    "TailoringDocumentListResponse",
    "JobDescriptionPasteRequest",
    "TailoringAnalysisRead",
    "TailoringAnalysisListResponse",
    "AnalyzeRequest",
    "ChatRequest",
    "ChatResponse",
    "TailoredCVRead",
    "TailoredCVListResponse",
    "GenerateCVRequest",
    "EditCVRequest",
    "EditCVClarification",
    "SetCVStatusRequest",
    "RevertCVRequest",
]
