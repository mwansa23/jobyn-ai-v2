"""Build downloadable PDF packages for job applications."""

from __future__ import annotations

import io
import logging
from typing import Any, Literal

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from backend.services.document_export import (
    DocumentExportError,
    generate_cover_letter_pdf,
    generate_pdf,
)

logger = logging.getLogger(__name__)

MAX_SUPPORTING_DOCUMENT_BYTES = 10 * 1024 * 1024
PackageType = Literal[
    "cover_letter_only",
    "cv_only",
    "cover_letter_cv",
    "complete_application_package",
]

PACKAGE_FILENAMES = {
    "cover_letter_only": "cover-letter.pdf",
    "cv_only": "tailored-cv.pdf",
    "cover_letter_cv": "application-package.pdf",
    "complete_application_package": "complete-application-package.pdf",
}


class ApplicationPackageError(Exception):
    """Raised when an application package cannot be assembled."""


def create_application_package(
    *,
    package_type: PackageType,
    cover_letter: str | None,
    cv_content: dict[str, Any] | None,
    supporting_documents: list[tuple[str, bytes]],
) -> tuple[bytes, str]:
    """Generate and merge package PDFs in the required document order."""
    include_cover_letter = package_type != "cv_only"
    include_cv = package_type != "cover_letter_only"
    include_supporting = package_type == "complete_application_package"

    if include_cover_letter and not (cover_letter and cover_letter.strip()):
        raise ApplicationPackageError(
            "Generate a cover letter before including it in the package."
        )
    if include_cv and cv_content is None:
        raise ApplicationPackageError(
            "Generate a tailored CV before including it in the package."
        )
    if include_supporting and not supporting_documents:
        raise ApplicationPackageError(
            "Upload at least one supporting PDF for the complete package."
        )
    if not include_supporting and supporting_documents:
        raise ApplicationPackageError(
            "Supporting PDFs can only be included in the complete package."
        )

    documents: list[tuple[str, bytes]] = []
    try:
        if include_cover_letter:
            documents.append(
                ("cover letter", generate_cover_letter_pdf(cover_letter or ""))
            )
        if include_cv:
            documents.append(("tailored CV", generate_pdf(cv_content or {})))
        for filename, pdf_bytes in supporting_documents:
            _validate_supporting_pdf(filename, pdf_bytes)
            documents.append((filename, pdf_bytes))

        if len(documents) == 1:
            return documents[0][1], PACKAGE_FILENAMES[package_type]
        return _merge_pdfs(documents), PACKAGE_FILENAMES[package_type]
    except (DocumentExportError, PdfReadError, ValueError, OSError) as exc:
        logger.exception("Application package generation failed")
        raise ApplicationPackageError(
            f"Could not generate the application package: {exc}"
        ) from exc


def _validate_supporting_pdf(filename: str, pdf_bytes: bytes) -> None:
    if not filename.lower().endswith(".pdf") or not pdf_bytes.startswith(b"%PDF-"):
        raise ApplicationPackageError(
            f"'{filename}' is not a valid PDF file."
        )
    if len(pdf_bytes) > MAX_SUPPORTING_DOCUMENT_BYTES:
        raise ApplicationPackageError(
            f"'{filename}' exceeds the 10 MB per-file limit."
        )


def _merge_pdfs(documents: list[tuple[str, bytes]]) -> bytes:
    writer = PdfWriter()
    for filename, pdf_bytes in documents:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
        if reader.is_encrypted:
            raise ApplicationPackageError(
                f"'{filename}' is password-protected and cannot be added."
            )
        if not reader.pages:
            raise ApplicationPackageError(f"'{filename}' contains no PDF pages.")
        writer.append(reader, import_outline=False)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
