"""CV Tailoring Studio — document upload and management endpoints.

All endpoints require authentication via :func:`get_current_user`.
Every document is associated with its owner; cross-user access is blocked
at the repository level.
"""

from __future__ import annotations

import logging
import uuid
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_session
from backend.models.enums import DocumentType
from backend.models.tailoring import TailoringDocument
from backend.models.user import User
from backend.repositories.tailoring import TailoringDocumentRepository
from backend.schemas.tailoring import (
    JobDescriptionPasteRequest,
    TailoringDocumentListResponse,
    TailoringDocumentRead,
)
from backend.services.tailoring_service import TailoringService, TailoringUploadError

router = APIRouter(prefix="/tailoring", tags=["CV Tailoring Studio"])

logger = logging.getLogger(__name__)


def _get_service(session: AsyncSession = Depends(get_session)) -> TailoringService:
    return TailoringService(
        TailoringDocumentRepository(session=session, model=TailoringDocument),
    )


def _safe_filename(filename: str) -> str:
    """Sanitise an uploaded filename to prevent path traversal."""
    # Strip any directory components.
    safe = filename.replace("\\", "/").rsplit("/", 1)[-1]
    # Keep only printable, non-path characters.
    return "".join(c for c in safe if c.isprintable() and c not in ("/", "\\"))[:255] or "document"


@router.post(
    "/cv/upload",
    response_model=TailoringDocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a CV file (PDF or DOCX)",
)
async def upload_cv(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    service: TailoringService = Depends(_get_service),
) -> TailoringDocumentRead:
    """Upload a CV in PDF or DOCX format. Maximum file size is 10 MB.

    The original file is preserved and text is extracted for use in
    subsequent tailoring steps.
    """
    content_type = file.content_type or ""
    filename = _safe_filename(file.filename or "cv")
    file_bytes = await file.read()

    try:
        doc = await service.upload_document(
            user_id=current_user.id,
            document_type=DocumentType.CV,
            filename=filename,
            content_type=content_type,
            file_bytes=file_bytes,
        )
    except TailoringUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return TailoringDocumentRead.model_validate(doc)


@router.post(
    "/job-description/upload",
    response_model=TailoringDocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a job-description file (PDF or DOCX)",
)
async def upload_job_description(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    service: TailoringService = Depends(_get_service),
) -> TailoringDocumentRead:
    """Upload a job description in PDF or DOCX format. Maximum file size is 10 MB.

    The original file is preserved and text is extracted for use in
    subsequent tailoring steps.
    """
    content_type = file.content_type or ""
    filename = _safe_filename(file.filename or "job-description")
    file_bytes = await file.read()

    try:
        doc = await service.upload_document(
            user_id=current_user.id,
            document_type=DocumentType.JOB_DESCRIPTION,
            filename=filename,
            content_type=content_type,
            file_bytes=file_bytes,
        )
    except TailoringUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return TailoringDocumentRead.model_validate(doc)


@router.post(
    "/job-description/paste",
    response_model=TailoringDocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Paste a job description as text",
)
async def paste_job_description(
    payload: JobDescriptionPasteRequest,
    current_user: User = Depends(get_current_user),
    service: TailoringService = Depends(_get_service),
) -> TailoringDocumentRead:
    """Create a job-description document from pasted text.

    No file upload is needed — the text is stored directly.
    """
    try:
        doc = await service.paste_job_description(
            user_id=current_user.id,
            text=payload.text,
        )
    except TailoringUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return TailoringDocumentRead.model_validate(doc)


@router.get(
    "/documents",
    response_model=TailoringDocumentListResponse,
    summary="List the current user's tailoring documents",
)
async def list_documents(
    document_type: str | None = None,
    offset: int = 0,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    service: TailoringService = Depends(_get_service),
) -> TailoringDocumentListResponse:
    """Return paginated documents for the authenticated user."""
    doc_type = None
    if document_type is not None:
        try:
            doc_type = DocumentType(document_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid document_type '{document_type}'. Use 'cv' or 'job_description'.",
            ) from None

    docs = await service.list_documents(
        current_user.id, document_type=doc_type, offset=offset, limit=limit,
    )
    return TailoringDocumentListResponse(
        items=[TailoringDocumentRead.model_validate(d) for d in docs],
        total=len(docs),
        offset=offset,
        limit=limit,
    )


@router.get(
    "/documents/{document_id}",
    response_model=TailoringDocumentRead,
    summary="Get a specific tailoring document",
)
async def get_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: TailoringService = Depends(_get_service),
) -> TailoringDocumentRead:
    """Return a single document owned by the authenticated user.

    Returns 404 if the document does not exist or belongs to another user.
    """
    doc = await service.get_document_for_user(document_id, current_user.id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )
    return TailoringDocumentRead.model_validate(doc)


@router.get(
    "/cv/latest",
    response_model=TailoringDocumentRead,
    summary="Get the user's latest CV document",
)
async def get_latest_cv(
    current_user: User = Depends(get_current_user),
    service: TailoringService = Depends(_get_service),
) -> TailoringDocumentRead:
    """Return the most recently uploaded CV document, or 404."""
    doc = await service.get_latest_cv(current_user.id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No CV document found. Please upload a CV first.",
        )
    return TailoringDocumentRead.model_validate(doc)


@router.get(
    "/job-description/latest",
    response_model=TailoringDocumentRead,
    summary="Get the user's latest job-description document",
)
async def get_latest_job_description(
    current_user: User = Depends(get_current_user),
    service: TailoringService = Depends(_get_service),
) -> TailoringDocumentRead:
    """Return the most recently created job-description document, or 404."""
    doc = await service.get_latest_job_description(current_user.id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No job description found. Please upload or paste one first.",
        )
    return TailoringDocumentRead.model_validate(doc)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tailoring document",
)
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: TailoringService = Depends(_get_service),
) -> None:
    """Soft-delete a document owned by the authenticated user.

    Returns 404 if the document does not exist or belongs to another user.
    """
    deleted = await service.delete_document(document_id, current_user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

# ── Phase 2: Analysis, matching, and recommendations ──────────────────────────

from backend.ai.cv_jd_matcher import CVJDMatcherError, CVJDMatcherService  # noqa: E402
from backend.ai.jd_analyzer import JDAnalysisError, JDAnalyzerService  # noqa: E402
from backend.ai.tailoring_advisor import TailoringAdvisorError, TailoringAdvisorService  # noqa: E402
from backend.models.tailoring_analysis import TailoringAnalysis  # noqa: E402
from backend.repositories.tailoring_analysis import TailoringAnalysisRepository  # noqa: E402
from backend.schemas.tailoring import (  # noqa: E402
    AnalyzeRequest,
    ChatRequest,
    ChatResponse,
    TailoringAnalysisListResponse,
    TailoringAnalysisRead,
)
from backend.services.tailoring_analysis_service import (  # noqa: E402
    TailoringAnalysisError,
    TailoringAnalysisService,
)


def _get_analysis_service(session: AsyncSession = Depends(get_session)) -> TailoringAnalysisService:
    return TailoringAnalysisService(
        repository=TailoringAnalysisRepository(session=session, model=TailoringAnalysis),
        document_service=TailoringService(
            TailoringDocumentRepository(session=session, model=TailoringDocument),
        ),
    )


@router.post(
    "/analyze",
    response_model=TailoringAnalysisRead,
    status_code=status.HTTP_201_CREATED,
    summary="Run CV-to-JD analysis (JD analysis + matching + recommendations)",
)
async def run_analysis(
    payload: AnalyzeRequest | None = None,
    current_user: User = Depends(get_current_user),
    service: TailoringAnalysisService = Depends(_get_analysis_service),
) -> TailoringAnalysisRead:
    """Run the full analysis pipeline: JD analysis → CV-to-JD matching → tailoring recommendations.

    Uses the user's latest CV and JD documents. All three stages are persisted
    in a single analysis record.
    """
    try:
        analysis = await service.run_analysis(current_user.id)
    except TailoringAnalysisError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return TailoringAnalysisRead.model_validate(analysis)


@router.get(
    "/analysis/latest",
    response_model=TailoringAnalysisRead,
    summary="Get the user's latest analysis result",
)
async def get_latest_analysis(
    current_user: User = Depends(get_current_user),
    service: TailoringAnalysisService = Depends(_get_analysis_service),
) -> TailoringAnalysisRead:
    """Return the most recent analysis for the authenticated user, or 404."""
    analysis = await service.get_latest_for_user(current_user.id)
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No analysis found. Run an analysis first.",
        )
    return TailoringAnalysisRead.model_validate(analysis)


@router.get(
    "/analysis/{analysis_id}",
    response_model=TailoringAnalysisRead,
    summary="Get a specific analysis result",
)
async def get_analysis(
    analysis_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: TailoringAnalysisService = Depends(_get_analysis_service),
) -> TailoringAnalysisRead:
    """Return a single analysis owned by the authenticated user, or 404."""
    analysis = await service.get_owned_by_id(analysis_id, current_user.id)
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found.",
        )
    return TailoringAnalysisRead.model_validate(analysis)


@router.get(
    "/analysis",
    response_model=TailoringAnalysisListResponse,
    summary="List the user's analyses",
)
async def list_analyses(
    offset: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    service: TailoringAnalysisService = Depends(_get_analysis_service),
) -> TailoringAnalysisListResponse:
    """Return paginated analyses for the authenticated user."""
    repo = TailoringAnalysisRepository(
        session=service.repository.session,
        model=TailoringAnalysis,
    )
    analyses = await repo.list_for_user(current_user.id, offset=offset, limit=limit)
    return TailoringAnalysisListResponse(
        items=[TailoringAnalysisRead.model_validate(a) for a in analyses],
        total=len(analyses),
        offset=offset,
        limit=limit,
    )

# ── Phase 3: Tailored CV generation, editing, export ─────────────────────────

from backend.ai.cv_editor import CVEditorError, CVEditorService  # noqa: E402
from backend.ai.cv_rewriter import CVRewriterError, CVRewriterService  # noqa: E402
from backend.models.tailored_cv import TailoredCV  # noqa: E402
from backend.repositories.tailored_cv import TailoredCVRepository  # noqa: E402
from backend.schemas.tailoring import (  # noqa: E402
    EditCVClarification,
    EditCVRequest,
    GenerateCVRequest,
    RevertCVRequest,
    SaveCVContentRequest,
    SetCVStatusRequest,
    TailoredCVListResponse,
    TailoredCVRead,
)
from backend.services.tailoring_generation_service import (  # noqa: E402
    TailoringGenerationError,
    TailoringGenerationService,
)
from backend.services.document_export import generate_docx, generate_pdf, DocumentExportError  # noqa: E402


def _get_generation_service(session: AsyncSession = Depends(get_session)) -> TailoringGenerationService:
    return TailoringGenerationService(
        repository=TailoredCVRepository(session=session, model=TailoredCV),
        document_service=TailoringService(
            TailoringDocumentRepository(session=session, model=TailoringDocument),
        ),
        analysis_repository=TailoringAnalysisRepository(
            session=session, model=TailoringAnalysis,
        ),
    )


@router.post(
    "/cv/generate",
    response_model=TailoredCVRead,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a tailored CV from the latest analysis",
)
async def generate_tailored_cv(
    payload: GenerateCVRequest | None = None,
    current_user: User = Depends(get_current_user),
    service: TailoringGenerationService = Depends(_get_generation_service),
) -> TailoredCVRead:
    """Generate a complete tailored CV using the Phase 2 analysis results.

    Uses the user's latest analysis (JD analysis + match + recommendations)
    to rewrite the original CV into a job-specific version.
    """
    try:
        cv = await service.generate_tailored_cv(current_user.id)
    except TailoringGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return TailoredCVRead.model_validate(cv)


@router.get(
    "/cv/tailored/latest",
    response_model=TailoredCVRead,
    summary="Get the user's latest tailored CV version",
)
async def get_latest_tailored_cv(
    current_user: User = Depends(get_current_user),
    service: TailoringGenerationService = Depends(_get_generation_service),
) -> TailoredCVRead:
    """Return the most recent tailored CV version, or 404."""
    cv = await service.get_latest_for_user(current_user.id)
    if cv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No tailored CV found. Generate one first.",
        )
    return TailoredCVRead.model_validate(cv)


@router.post(
    "/cv/edit",
    response_model=TailoredCVRead | EditCVClarification,
    summary="Apply a conversational edit to the tailored CV",
)
async def edit_tailored_cv(
    payload: EditCVRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    service: TailoringGenerationService = Depends(_get_generation_service),
) -> TailoredCVRead | EditCVClarification:
    """Apply a natural-language edit request to the latest tailored CV.

    Creates a new version with the updated content. Previous versions are preserved.
    """
    try:
        result = await service.apply_edit(current_user.id, payload.message)
    except TailoringGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if isinstance(result, dict):
        return EditCVClarification.model_validate(result)

    response.status_code = status.HTTP_201_CREATED
    return TailoredCVRead.model_validate(result)


@router.get(
    "/cv/versions",
    response_model=TailoredCVListResponse,
    summary="List all tailored CV versions",
)
async def list_cv_versions(
    offset: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    service: TailoringGenerationService = Depends(_get_generation_service),
) -> TailoredCVListResponse:
    """Return paginated tailored CV versions for the authenticated user."""
    versions = await service.list_versions(current_user.id, offset=offset, limit=limit)
    return TailoredCVListResponse(
        items=[TailoredCVRead.model_validate(v) for v in versions],
        total=len(versions),
        offset=offset,
        limit=limit,
    )


@router.get(
    "/cv/{cv_id}",
    response_model=TailoredCVRead,
    summary="Get a specific tailored CV version",
)
async def get_tailored_cv(
    cv_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: TailoringGenerationService = Depends(_get_generation_service),
) -> TailoredCVRead:
    """Return a specific tailored CV version owned by the authenticated user."""
    cv = await service.get_owned_by_id(cv_id, current_user.id)
    if cv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tailored CV not found.",
        )
    response_data = TailoredCVRead.model_validate(cv)
    latest = await service.get_latest_for_user(current_user.id)
    response_data.content = deepcopy(response_data.content)
    response_data.content["comparison"] = service.build_comparison(
        latest.content.get("sections", []) if latest else [],
        response_data.content.get("sections", []),
        {},
        fallback_explanation=(
            f"Previewing version {cv.version_number} against current version "
            f"{latest.version_number}."
            if latest
            else "Previewing the saved CV version."
        ),
    )
    return response_data


@router.post(
    "/cv/{cv_id}/visual-edit",
    response_model=TailoredCVRead,
    status_code=status.HTTP_201_CREATED,
    summary="Save visual edits as a new tailored CV version",
)
async def save_visual_cv_edit(
    cv_id: uuid.UUID,
    payload: SaveCVContentRequest,
    current_user: User = Depends(get_current_user),
    service: TailoringGenerationService = Depends(_get_generation_service),
) -> TailoredCVRead:
    """Persist candidate-edited CV sections without an AI rewrite."""
    try:
        cv = await service.save_visual_edit(
            current_user.id,
            cv_id,
            [section.model_dump() for section in payload.sections],
        )
    except TailoringGenerationError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if message == "Tailored CV not found."
            else status.HTTP_409_CONFLICT
            if message.startswith("This CV version is no longer current.")
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(status_code=code, detail=message) from exc
    return TailoredCVRead.model_validate(cv)


@router.patch(
    "/cv/{cv_id}/status",
    response_model=TailoredCVRead,
    summary="Accept or reject a tailored CV version",
)
async def set_cv_status(
    cv_id: uuid.UUID,
    payload: SetCVStatusRequest,
    current_user: User = Depends(get_current_user),
    service: TailoringGenerationService = Depends(_get_generation_service),
) -> TailoredCVRead:
    """Set the status of a tailored CV version to 'accepted' or 'rejected'."""
    try:
        cv = await service.set_status(cv_id, current_user.id, payload.status)
    except TailoringGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if cv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tailored CV not found.",
        )
    return TailoredCVRead.model_validate(cv)


@router.post(
    "/cv/revert",
    response_model=TailoredCVRead,
    status_code=status.HTTP_201_CREATED,
    summary="Revert to an earlier CV version",
)
async def revert_cv(
    payload: RevertCVRequest,
    current_user: User = Depends(get_current_user),
    service: TailoringGenerationService = Depends(_get_generation_service),
) -> TailoredCVRead:
    """Create a new version with the content of an earlier version.

    This effectively undoes later changes. The original versions remain preserved.
    """
    try:
        previous = await service.get_latest_for_user(current_user.id)
        cv = await service.revert_to_version(current_user.id, payload.version_number)
    except TailoringGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    response_data = TailoredCVRead.model_validate(cv)
    response_data.content = deepcopy(response_data.content)
    response_data.content["comparison"] = service.build_comparison(
        previous.content.get("sections", []) if previous else [],
        response_data.content.get("sections", []),
        {},
        fallback_explanation=f"Restored from version {payload.version_number}.",
    )
    return response_data


@router.get(
    "/cv/{cv_id}/export/docx",
    summary="Download the tailored CV as a DOCX file",
)
async def export_docx(
    cv_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: TailoringGenerationService = Depends(_get_generation_service),
):
    """Export the tailored CV as a DOCX file download.

    Returns a binary DOCX file. The content matches the approved CV content.
    """
    from fastapi.responses import Response

    cv = await service.get_owned_by_id(cv_id, current_user.id)
    if cv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tailored CV not found.",
        )

    try:
        docx_bytes = generate_docx(cv.content)
    except DocumentExportError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename=tailored_cv_v{cv.version_number}.docx"},
    )


@router.get(
    "/cv/{cv_id}/export/pdf",
    summary="Download the tailored CV as a PDF file",
)
async def export_pdf(
    cv_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: TailoringGenerationService = Depends(_get_generation_service),
):
    """Export the tailored CV as a PDF file download.

    Returns a binary PDF file. The content matches the approved CV content.
    """
    from fastapi.responses import Response

    cv = await service.get_owned_by_id(cv_id, current_user.id)
    if cv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tailored CV not found.",
        )

    try:
        pdf_bytes = generate_pdf(cv.content)
    except DocumentExportError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=tailored_cv_v{cv.version_number}.pdf"},
    )
