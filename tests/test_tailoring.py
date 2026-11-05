"""Tests for the CV Tailoring Studio — document upload, extraction, and management.

Uses the session-scoped ``client`` fixture from conftest.py and generates
unique emails per test to avoid UNIQUE-constraint collisions.
All file bytes are generated in-memory — no real files are read from disk.
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient

# ── Constants ─────────────────────────────────────────────────────────────────

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"

CV_UPLOAD_URL = "/api/v1/tailoring/cv/upload"
JD_UPLOAD_URL = "/api/v1/tailoring/job-description/upload"
JD_PASTE_URL = "/api/v1/tailoring/job-description/paste"
DOCS_URL = "/api/v1/tailoring/documents"
CV_LATEST_URL = "/api/v1/tailoring/cv/latest"
JD_LATEST_URL = "/api/v1/tailoring/job-description/latest"

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_CT = "application/pdf"

# ── Test fixtures ──────────────────────────────────────────────────────────────

def _unique_email() -> str:
    return f"tailoring.{uuid.uuid4().hex[:8]}@example.com"


def _register_and_login(client: TestClient) -> str:
    """Register a fresh user and return their access token."""
    email = _unique_email()
    pw = "TestPassword123!"
    r = client.post(REGISTER_URL, json={"email": email, "password": pw, "full_name": "Test User"})
    assert r.status_code == 201, r.text
    r = client.post(LOGIN_URL, json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_docx_bytes(text: str = "Test content for DOCX file.") -> bytes:
    """Generate a minimal valid DOCX in-memory using python-docx."""
    from docx import Document
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_pdf_bytes(text: str = "Test content for PDF file.") -> bytes:
    """Generate a minimal valid PDF in-memory that pypdf can extract text from."""
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET"
    content_bytes = content.encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content_bytes)} >>\nstream\n".encode("latin-1") + content_bytes + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    pdf = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_offset = len(pdf)
    pdf += b"xref\n"
    pdf += f"0 {len(objects) + 1}\n".encode()
    pdf += b"0000000000 65535 f \n"
    for offset in offsets:
        pdf += f"{offset:010d} 00000 n \n".encode()

    pdf += b"trailer\n"
    pdf += f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    pdf += b"startxref\n"
    pdf += f"{xref_offset}\n".encode()
    pdf += b"%%EOF"

    return pdf


# ── Test Classes ───────────────────────────────────────────────────────────────


class TestCVUpload:
    """Tests for CV upload via POST /tailoring/cv/upload."""

    def test_docx_cv_upload_succeeds(self, client: TestClient) -> None:
        token = _register_and_login(client)
        docx_bytes = _make_docx_bytes("Software Engineer with 5 years experience in Python.")
        r = client.post(
            CV_UPLOAD_URL,
            headers=_auth_header(token),
            files={"file": ("cv.docx", docx_bytes, DOCX_CT)},
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["document_type"] == "cv"
        assert data["extraction_status"] == "succeeded"
        assert data["extracted_text"] is not None
        assert len(data["extracted_text"]) > 0
        assert data["original_filename"] == "cv.docx"
        assert data["file_size_bytes"] == len(docx_bytes)

    def test_pdf_cv_upload_succeeds(self, client: TestClient) -> None:
        token = _register_and_login(client)
        pdf_bytes = _make_pdf_bytes("Data Analyst experienced in SQL and Python.")
        r = client.post(
            CV_UPLOAD_URL,
            headers=_auth_header(token),
            files={"file": ("cv.pdf", pdf_bytes, PDF_CT)},
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["document_type"] == "cv"
        assert data["extraction_status"] == "succeeded"
        assert data["extracted_text"] is not None

    def test_multiple_cv_uploads_preserve_all_versions(self, client: TestClient) -> None:
        """Uploading a second CV does not overwrite the first."""
        token = _register_and_login(client)
        # First upload
        docx1 = _make_docx_bytes("First CV version.")
        r1 = client.post(CV_UPLOAD_URL, headers=_auth_header(token),
                         files={"file": ("v1.docx", docx1, DOCX_CT)})
        assert r1.status_code == 201
        first_id = r1.json()["id"]
        # Second upload
        docx2 = _make_docx_bytes("Second CV version with more experience.")
        r2 = client.post(CV_UPLOAD_URL, headers=_auth_header(token),
                         files={"file": ("v2.docx", docx2, DOCX_CT)})
        assert r2.status_code == 201
        second_id = r2.json()["id"]
        # Both should exist and be different
        assert first_id != second_id
        # List should show both
        r_list = client.get(DOCS_URL, headers=_auth_header(token),
                            params={"document_type": "cv"})
        assert r_list.status_code == 200
        assert r_list.json()["total"] >= 2


class TestJobDescriptionUpload:
    """Tests for job-description upload and paste."""

    def test_docx_job_description_upload_succeeds(self, client: TestClient) -> None:
        token = _register_and_login(client)
        docx_bytes = _make_docx_bytes("Senior Python Developer needed for growing team.")
        r = client.post(
            JD_UPLOAD_URL,
            headers=_auth_header(token),
            files={"file": ("jd.docx", docx_bytes, DOCX_CT)},
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["document_type"] == "job_description"
        assert data["extraction_status"] == "succeeded"
        assert data["extracted_text"] is not None

    def test_pdf_job_description_upload_succeeds(self, client: TestClient) -> None:
        token = _register_and_login(client)
        pdf_bytes = _make_pdf_bytes("Looking for a full-stack engineer.")
        r = client.post(
            JD_UPLOAD_URL,
            headers=_auth_header(token),
            files={"file": ("jd.pdf", pdf_bytes, PDF_CT)},
        )
        assert r.status_code == 201, r.text
        assert r.json()["document_type"] == "job_description"

    def test_pasted_job_description_succeeds(self, client: TestClient) -> None:
        token = _register_and_login(client)
        text = "We are looking for a Senior Backend Engineer with experience in Python, FastAPI, and PostgreSQL."
        r = client.post(
            JD_PASTE_URL,
            headers=_auth_header(token),
            json={"text": text},
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["document_type"] == "job_description"
        assert data["extraction_status"] == "succeeded"
        assert data["pasted_text"] == text
        assert data["extracted_text"] == text
        assert data["original_filename"] is None
        assert data["content_type"] is None

    def test_pasted_empty_job_description_rejected(self, client: TestClient) -> None:
        token = _register_and_login(client)
        r = client.post(
            JD_PASTE_URL,
            headers=_auth_header(token),
            json={"text": "   "},
        )
        assert r.status_code == 422


class TestValidationAndErrors:
    """Tests for file validation, unsupported formats, and oversized files."""

    def test_unsupported_file_format_rejected(self, client: TestClient) -> None:
        token = _register_and_login(client)
        r = client.post(
            CV_UPLOAD_URL,
            headers=_auth_header(token),
            files={"file": ("cv.txt", b"plain text", "text/plain")},
        )
        assert r.status_code == 422
        assert "Unsupported file type" in r.json()["detail"]

    def test_empty_file_rejected(self, client: TestClient) -> None:
        token = _register_and_login(client)
        r = client.post(
            CV_UPLOAD_URL,
            headers=_auth_header(token),
            files={"file": ("empty.pdf", b"", PDF_CT)},
        )
        assert r.status_code == 422


class TestAuthenticationAndOwnership:
    """Tests for authentication requirements and cross-user access prevention."""

    def test_unauthenticated_cv_upload_rejected(self, client: TestClient) -> None:
        docx_bytes = _make_docx_bytes()
        r = client.post(CV_UPLOAD_URL, files={"file": ("cv.docx", docx_bytes, DOCX_CT)})
        assert r.status_code == 401

    def test_unauthenticated_jd_paste_rejected(self, client: TestClient) -> None:
        r = client.post(JD_PASTE_URL, json={"text": "test"})
        assert r.status_code == 401

    def test_cross_user_document_access_prevented(self, client: TestClient) -> None:
        """User A's documents are not accessible by User B."""
        # User A uploads a CV
        token_a = _register_and_login(client)
        docx_bytes = _make_docx_bytes("User A's private CV content.")
        r_a = client.post(CV_UPLOAD_URL, headers=_auth_header(token_a),
                          files={"file": ("cv.docx", docx_bytes, DOCX_CT)})
        assert r_a.status_code == 201
        doc_id = r_a.json()["id"]

        # User B tries to access it
        token_b = _register_and_login(client)
        r_b = client.get(f"{DOCS_URL}/{doc_id}", headers=_auth_header(token_b))
        assert r_b.status_code == 404

    def test_cross_user_delete_prevented(self, client: TestClient) -> None:
        """User B cannot delete User A's document."""
        token_a = _register_and_login(client)
        docx_bytes = _make_docx_bytes("User A's CV.")
        r_a = client.post(CV_UPLOAD_URL, headers=_auth_header(token_a),
                          files={"file": ("cv.docx", docx_bytes, DOCX_CT)})
        doc_id = r_a.json()["id"]

        token_b = _register_and_login(client)
        r_del = client.delete(f"{DOCS_URL}/{doc_id}", headers=_auth_header(token_b))
        assert r_del.status_code == 404


class TestDocumentManagement:
    """Tests for listing, retrieving, and deleting documents."""

    def test_list_documents_returns_only_user_own(self, client: TestClient) -> None:
        token = _register_and_login(client)
        # Upload a CV and a JD
        docx = _make_docx_bytes("My CV.")
        client.post(CV_UPLOAD_URL, headers=_auth_header(token),
                    files={"file": ("cv.docx", docx, DOCX_CT)})
        client.post(JD_PASTE_URL, headers=_auth_header(token),
                     json={"text": "Job description text."})

        r = client.get(DOCS_URL, headers=_auth_header(token))
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 2

    def test_get_latest_cv(self, client: TestClient) -> None:
        token = _register_and_login(client)
        docx = _make_docx_bytes("Latest CV content.")
        client.post(CV_UPLOAD_URL, headers=_auth_header(token),
                    files={"file": ("cv.docx", docx, DOCX_CT)})

        r = client.get(CV_LATEST_URL, headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()["document_type"] == "cv"

    def test_get_latest_cv_returns_404_when_none_exists(self, client: TestClient) -> None:
        token = _register_and_login(client)
        r = client.get(CV_LATEST_URL, headers=_auth_header(token))
        assert r.status_code == 404

    def test_get_latest_job_description(self, client: TestClient) -> None:
        token = _register_and_login(client)
        client.post(JD_PASTE_URL, headers=_auth_header(token),
                     json={"text": "Senior developer position."})

        r = client.get(JD_LATEST_URL, headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()["document_type"] == "job_description"

    def test_delete_document_succeeds(self, client: TestClient) -> None:
        token = _register_and_login(client)
        docx = _make_docx_bytes("To be deleted.")
        r_upload = client.post(CV_UPLOAD_URL, headers=_auth_header(token),
                                files={"file": ("cv.docx", docx, DOCX_CT)})
        doc_id = r_upload.json()["id"]

        r_del = client.delete(f"{DOCS_URL}/{doc_id}", headers=_auth_header(token))
        assert r_del.status_code == 204

        # Verify it's gone from the list
        r_get = client.get(f"{DOCS_URL}/{doc_id}", headers=_auth_header(token))
        assert r_get.status_code == 404


class TestSafeFilename:
    """Tests for filename sanitisation and path traversal prevention."""

    def test_path_traversal_filename_sanitized(self, client: TestClient) -> None:
        token = _register_and_login(client)
        docx = _make_docx_bytes("Safe content.")
        r = client.post(
            CV_UPLOAD_URL,
            headers=_auth_header(token),
            files={"file": ("../../../etc/passwd.docx", docx, DOCX_CT)},
        )
        assert r.status_code == 201
        filename = r.json()["original_filename"]
        assert "/" not in filename
        assert ".." not in filename
        assert "passwd.docx" in filename
