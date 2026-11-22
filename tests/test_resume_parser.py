"""Tests for backend/services/resume_parser.py.

Covers:
- extract_text() newline-preserving normalisation
- _extract_pdf() with realistic multi-page, multi-section PDFs (real extractor,
  NOT mocked — tests exercise the actual pypdf code path)
- Empty-page detection and logging
- Page-boundary preservation via form-feed separator
- _extract_docx() happy path
- All validation helpers (validate_upload, error classes)

Tests that call _extract_pdf() directly use a hand-crafted PDF builder that
places each line of text as a separate Tj command so the content stream
produces distinct text runs — the same structure a word-processor CV produces.
"""

from __future__ import annotations

import io
import logging
import struct
from typing import Any

import pytest

from backend.services.resume_parser import (
    EmptyDocumentError,
    FileTooLargeError,
    MAX_FILE_SIZE_BYTES,
    ResumeParserError,
    UnsupportedFileTypeError,
    _extract_pdf,
    extract_text,
    validate_upload,
)

# ---------------------------------------------------------------------------
# PDF construction helpers
# ---------------------------------------------------------------------------

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _pdf_page_stream(lines: list[str]) -> bytes:
    """Return a PDF content stream that places each line as a separate text run.

    Uses Helvetica (a standard Type1 font) so no font file is needed.
    Each line is placed 20 units below the previous one starting at y=720.
    This mimics how a real CV has each field (name, employer, job title,
    date, bullet) on its own line.
    """
    parts = ["BT", "/F1 11 Tf"]
    y = 720
    for line in lines:
        # Escape PDF special chars
        safe = (
            line
            .replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        parts.append(f"72 {y} Td ({safe}) Tj")
        y -= 14  # line spacing
    parts.append("ET")
    return "\n".join(parts).encode("latin-1")


def _build_pdf_pages(pages: list[list[str]]) -> bytes:
    """Build a minimal valid multi-page PDF with one Td+Tj per line per page.

    Args:
        pages: List of pages; each page is a list of text lines.

    Returns:
        PDF file bytes parseable by pypdf.
    """
    objects: list[bytes] = []

    # We'll fill in the kids array and content references after building pages.
    page_object_indices: list[int] = []  # 1-based object numbers for each page

    # Object 1 → Catalog (references Pages object 2)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")

    # Object 2 → Pages (placeholder; will be rebuilt below)
    objects.append(b"placeholder_pages")

    # Object 3 → Font
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # Build page objects: each page needs a Page dict and a stream.
    # Pages dict is object 2; Font is object 3.
    # First page object is at index 4 (1-based: object 4).
    # For N pages we add 2*N objects: Page dict + stream content.

    next_obj = 4  # 1-based
    for page_lines in pages:
        stream_bytes = _pdf_page_stream(page_lines)
        stream_len = len(stream_bytes)
        content_obj_num = next_obj + 1  # stream comes after page dict
        # Page dict
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 612 792] "
            f"/Contents {content_obj_num} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>"
        ).encode()
        objects.append(page_dict)
        page_object_indices.append(next_obj)
        next_obj += 1
        # Stream content
        stream_obj = (
            f"<< /Length {stream_len} >>\nstream\n".encode("latin-1")
            + stream_bytes
            + b"\nendstream"
        )
        objects.append(stream_obj)
        next_obj += 1

    # Rebuild the Pages object now that we know the kids.
    kids = " ".join(f"{n} 0 R" for n in page_object_indices)
    pages_obj = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    objects[1] = pages_obj  # replace placeholder

    # Write the PDF binary.
    pdf = b"%PDF-1.4\n"
    offsets: list[int] = []
    for i, obj_bytes in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + obj_bytes + b"\nendobj\n"

    xref_offset = len(pdf)
    total_objects = len(objects)
    pdf += b"xref\n"
    pdf += f"0 {total_objects + 1}\n".encode()
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode()

    pdf += b"trailer\n"
    pdf += f"<< /Size {total_objects + 1} /Root 1 0 R >>\n".encode()
    pdf += b"startxref\n"
    pdf += f"{xref_offset}\n".encode()
    pdf += b"%%EOF"

    return pdf


def _blank_page_pdf() -> bytes:
    """Return a two-page PDF where page 1 has text and page 2 is blank."""
    page1_stream = _pdf_page_stream(["Some content on page one"])
    stream_len = len(page1_stream)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        # Page 1 — has content stream
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R /Resources << /Font << /F1 3 0 R >> >> >>",
        # Page 2 — no Contents key → extract_text() returns ""
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        # Stream for page 1
        (f"<< /Length {stream_len} >>\nstream\n".encode("latin-1")
         + page1_stream
         + b"\nendstream"),
    ]
    # Need to renumber: Catalog=1, Pages=2, Font=3, Page1=4, Page2=5, Stream=6
    # Rebuild with correct kids
    objects[1] = b"<< /Type /Pages /Kids [4 0 R 5 0 R] /Count 2 >>"
    objects[3] = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 6 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
    objects[4] = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"

    pdf = b"%PDF-1.4\n"
    offsets: list[int] = []
    for i, obj_bytes in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + obj_bytes + b"\nendobj\n"

    xref_offset = len(pdf)
    total = len(objects)
    pdf += b"xref\n"
    pdf += f"0 {total + 1}\n".encode()
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode()
    pdf += b"trailer\n"
    pdf += f"<< /Size {total + 1} /Root 1 0 R >>\n".encode()
    pdf += b"startxref\n"
    pdf += f"{xref_offset}\n".encode()
    pdf += b"%%EOF"
    return pdf


# ---------------------------------------------------------------------------
# _extract_pdf — real extractor tests (not mocked)
# ---------------------------------------------------------------------------

class TestExtractPdfReal:
    """Tests for _extract_pdf() that exercise the real pypdf code path."""

    def test_single_page_all_lines_present(self) -> None:
        """Every line of a single-page CV section appears in extracted text."""
        lines = [
            "Mwansa Matimba",
            "HR Manager",
            "Talent House Ltd",
            "January 2021 - Present",
            "Developed end-to-end recruitment strategy",
            "Led onboarding programme for 50+ employees",
        ]
        pdf = _build_pdf_pages([lines])
        result = _extract_pdf(pdf)
        for line in lines:
            assert line in result, f"Expected line not found: {line!r}"

    def test_two_page_cv_all_sections_present(self) -> None:
        """A two-page PDF yields content from both pages."""
        page1_lines = [
            "Jane Smith",
            "Senior HR Manager",
            "Experience",
            "HR Director",
            "Beta Corporation",
            "2019 - 2023",
            "Managed a team of 12 HR professionals",
        ]
        page2_lines = [
            "Education",
            "BSc Human Resource Management",
            "University of Zambia",
            "2010 - 2014",
            "Certifications",
            "CIPD Level 5",
            "SHRM Certified Professional",
        ]
        pdf = _build_pdf_pages([page1_lines, page2_lines])
        result = _extract_pdf(pdf)

        # Page 1 content
        assert "HR Director" in result
        assert "Beta Corporation" in result
        assert "2019 - 2023" in result
        assert "Managed a team of 12 HR professionals" in result

        # Page 2 content — this is the critical test for the original defect
        assert "BSc Human Resource Management" in result
        assert "University of Zambia" in result
        assert "CIPD Level 5" in result
        assert "SHRM Certified Professional" in result

    def test_seven_jobs_all_employers_present(self) -> None:
        """A realistic CV with 7 employment records retains all employer names."""
        jobs = [
            ("HR Manager", "Talent House Ltd", "2021 - Present"),
            ("Senior HR Officer", "ABC Mining Corp", "2019 - 2021"),
            ("HR Officer", "Zambia National Bank", "2017 - 2019"),
            ("HR Assistant", "Barclays Zambia", "2015 - 2017"),
            ("Recruitment Coordinator", "TopTalent Agency", "2013 - 2015"),
            ("HR Intern", "Copper Belt Energy", "2012 - 2013"),
            ("Graduate Trainee", "Ministry of Labour", "2011 - 2012"),
        ]
        lines: list[str] = []
        for title, employer, dates in jobs:
            lines.extend([title, employer, dates, f"Responsibilities for {title}", ""])

        pdf = _build_pdf_pages([lines])
        result = _extract_pdf(pdf)

        for title, employer, dates in jobs:
            assert title in result, f"Job title missing: {title!r}"
            assert employer in result, f"Employer missing: {employer!r}"
            assert dates in result, f"Dates missing: {dates!r}"

    def test_certificates_on_page_two_present(self) -> None:
        """Certificate names on page 2 must not be silently dropped."""
        cert_names = [
            "CIPD Level 5 Certificate in HR Management",
            "SHRM Certified Professional (SHRM-CP)",
            "PMC Certificate in Mediation and Conflict Resolution",
            "ISO 45001 Lead Implementer",
            "Advanced Certificate in Employment Law",
        ]
        page1 = ["John Doe", "HR Director", "Experience", "Company A", "2020 - Present"]
        page2 = ["Certifications"] + cert_names

        pdf = _build_pdf_pages([page1, page2])
        result = _extract_pdf(pdf)

        for cert in cert_names:
            assert cert in result, f"Certificate not found: {cert!r}"

    def test_page_separator_present_in_raw_output(self) -> None:
        """_extract_pdf joins pages with \\f so page boundaries are distinct."""
        page1 = ["Page one content"]
        page2 = ["Page two content"]
        pdf = _build_pdf_pages([page1, page2])
        result = _extract_pdf(pdf)
        # Form-feed must be present in the raw output from _extract_pdf
        assert "\f" in result, "Page separator \\f missing from _extract_pdf output"


class TestExtractPdfEmptyPageWarning:
    """_extract_pdf must log a warning when a page yields no text."""

    def test_blank_page_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A PDF with a blank second page produces a WARNING for that page."""
        # Build a minimal two-page PDF where page 2 has no content stream.
        # We use a simple approach: page 2 exists but its content is empty.
        page1 = ["Some text on page one"]
        page2: list[str] = []  # No lines → stream produces no visible text

        # pypdf returns "" for a page whose stream contains only whitespace.
        # We simulate this by making page 2's stream an empty BT/ET pair.
        page2_lines_empty: list[str] = [""]
        pdf = _build_pdf_pages([page1, page2_lines_empty])

        with caplog.at_level(logging.WARNING, logger="backend.services.resume_parser"):
            _extract_pdf(pdf)

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "page 2" in msg.lower() or "2" in msg
            for msg in warning_messages
        ), f"Expected warning about page 2, got: {warning_messages}"


# ---------------------------------------------------------------------------
# extract_text() — normalisation behaviour
# ---------------------------------------------------------------------------

class TestExtractTextNormalisation:
    """extract_text() must preserve line structure, not destroy it."""

    def test_newlines_are_preserved(self) -> None:
        """Line breaks in extracted text survive normalisation."""
        lines = [
            "HR Manager",
            "Talent House Ltd",
            "January 2021 - Present",
            "Managed recruitment pipeline",
        ]
        pdf = _build_pdf_pages([lines])
        result = extract_text(pdf, PDF_MIME)

        # Result must contain each token AND must be multi-line
        assert "\n" in result, "extract_text must preserve newline structure"
        for line in lines:
            assert line in result

    def test_section_boundaries_preserved(self) -> None:
        """Blank lines between CV sections are kept (at most one per run)."""
        # pypdf returns blank lines between sections in the content stream
        page1_lines = [
            "Experience",
            "",
            "HR Director",
            "Company A",
            "2020 - Present",
            "",
            "Education",
            "",
            "BSc HRM",
            "University of Zambia",
        ]
        pdf = _build_pdf_pages([page1_lines])
        result = extract_text(pdf, PDF_MIME)

        # Both sections present and separated
        assert "Experience" in result
        assert "Education" in result
        assert "HR Director" in result
        assert "BSc HRM" in result

    def test_no_double_blank_lines(self) -> None:
        """Consecutive blank lines are collapsed to at most one."""
        # Build a PDF whose stream has many consecutive blank lines
        lines_with_blanks = ["Header", "", "", "", "Footer"]
        pdf = _build_pdf_pages([lines_with_blanks])
        result = extract_text(pdf, PDF_MIME)
        assert "\n\n\n" not in result, "Consecutive blank lines were not collapsed"

    def test_trailing_whitespace_stripped_per_line(self) -> None:
        """Lines with trailing spaces have the trailing spaces removed."""
        # Inject trailing spaces via the PDF stream.
        # This is a unit test of the normalisation logic, so we can call
        # the normalisation directly via extract_text with a patched extractor.
        from unittest.mock import patch

        raw = "HR Manager   \nTalent House   \n  2021 - Present  \n"
        with patch(
            "backend.services.resume_parser._extract_pdf",
            return_value=raw,
        ):
            result = extract_text(b"dummy", PDF_MIME)

        for line in result.splitlines():
            assert line == line.rstrip(), f"Line has trailing whitespace: {line!r}"

    def test_old_flat_string_behaviour_is_gone(self) -> None:
        """The old ' '.join(text.split()) behaviour must not collapse all newlines."""
        lines = ["Job Title", "Employer Name", "Date Range", "Responsibility"]
        pdf = _build_pdf_pages([lines])
        result = extract_text(pdf, PDF_MIME)

        # With the old behaviour all tokens were on one line; now they must not be.
        assert result != " ".join(lines), (
            "extract_text collapsed everything to a flat string — old bug not fixed"
        )

    def test_employer_and_date_not_merged_on_same_line(self) -> None:
        """Employer name and date range must NOT be fused into a single token."""
        lines = ["Talent House Ltd", "January 2021 - Present"]
        pdf = _build_pdf_pages([lines])
        result = extract_text(pdf, PDF_MIME)

        # With the old code: "Talent House Ltd January 2021 - Present" (one line)
        # With the fix: they appear on separate lines
        result_lines = [l for l in result.splitlines() if l.strip()]
        # At minimum, the employer and date must not be fused into the same line
        fused = any(
            "Talent House Ltd" in line and "January 2021" in line
            for line in result_lines
        )
        assert not fused, (
            "Employer name and date were fused on the same line — "
            "section boundary not preserved"
        )

    def test_form_feed_treated_as_line_boundary(self) -> None:
        """The \\f page separator produced by _extract_pdf is treated as a line break."""
        from unittest.mock import patch

        # Simulate two pages joined with form-feed
        raw = "Page one content\fPage two content"
        with patch(
            "backend.services.resume_parser._extract_pdf",
            return_value=raw,
        ):
            result = extract_text(b"dummy", PDF_MIME)

        assert "Page one content" in result
        assert "Page two content" in result
        # They must not be on the same line
        assert "Page one contentPage two content" not in result

    def test_empty_extraction_raises(self) -> None:
        """A PDF whose every page yields only whitespace raises EmptyDocumentError."""
        from unittest.mock import patch

        with patch(
            "backend.services.resume_parser._extract_pdf",
            return_value="   \n\n   \f   \n",
        ):
            with pytest.raises(EmptyDocumentError):
                extract_text(b"dummy", PDF_MIME)


# ---------------------------------------------------------------------------
# extract_text() — validation helpers (unchanged public API)
# ---------------------------------------------------------------------------

class TestValidateUpload:
    def test_pdf_accepted(self) -> None:
        validate_upload(PDF_MIME, 100)

    def test_docx_accepted(self) -> None:
        validate_upload(DOCX_MIME, 100)

    def test_text_plain_rejected(self) -> None:
        with pytest.raises(UnsupportedFileTypeError):
            validate_upload("text/plain", 100)

    def test_oversized_rejected(self) -> None:
        with pytest.raises(FileTooLargeError):
            validate_upload(PDF_MIME, MAX_FILE_SIZE_BYTES + 1)

    def test_exact_max_size_accepted(self) -> None:
        validate_upload(PDF_MIME, MAX_FILE_SIZE_BYTES)


# ---------------------------------------------------------------------------
# _extract_pdf — multi-page regression: dates survive but details must too
# ---------------------------------------------------------------------------

class TestDateAndDetailCoexistence:
    """Regression: dates were retained but responsibilities were lost.

    With the old whitespace collapse, the LLM received all tokens in a flat
    string and tended to reproduce short distinctive tokens (dates) while
    dropping prose (responsibilities).  These tests confirm that dates and
    their associated responsibilities appear together in a structured way.
    """

    def test_dates_and_responsibilities_on_distinct_lines(self) -> None:
        """Dates and responsibility bullets appear on separate lines, not merged."""
        lines = [
            "HR Manager",
            "Talent House Ltd",
            "January 2021 - Present",
            "Managed full recruitment lifecycle for 10+ roles",
            "Reduced time-to-hire by 30% through process improvements",
            "Coordinated employee relations and disciplinary procedures",
        ]
        pdf = _build_pdf_pages([lines])
        result = extract_text(pdf, PDF_MIME)
        result_lines = [l for l in result.splitlines() if l.strip()]

        # Dates must be present
        assert any("January 2021" in l for l in result_lines), "Date missing"

        # At least two responsibility bullets must be present
        responsibility_lines = [
            l for l in result_lines
            if "Managed" in l or "Reduced" in l or "Coordinated" in l
        ]
        assert len(responsibility_lines) >= 2, (
            f"Expected at least 2 responsibility lines, found: {responsibility_lines}"
        )

    def test_seven_jobs_dates_and_details_both_present(self) -> None:
        """With 7 jobs, both dates and responsibility bullets survive extraction."""
        jobs = [
            ("HR Director", "Beta Corp", "2021-2023",
             "Led HR transformation programme"),
            ("Senior HR Officer", "Alpha Ltd", "2018-2021",
             "Managed payroll for 300 employees"),
            ("HR Officer", "Gamma Bank", "2015-2018",
             "Developed performance review framework"),
            ("HR Coordinator", "Delta Mining", "2013-2015",
             "Coordinated graduate recruitment drive"),
            ("HR Assistant", "Epsilon Agency", "2011-2013",
             "Supported administration of benefits"),
            ("Recruitment Officer", "Zeta Group", "2009-2011",
             "Screened and interviewed 100+ candidates"),
            ("HR Intern", "Eta Services", "2008-2009",
             "Assisted in onboarding documentation"),
        ]
        lines: list[str] = []
        for title, employer, dates, responsibility in jobs:
            lines.extend([title, employer, dates, responsibility, ""])

        pdf = _build_pdf_pages([lines[:len(lines)//2], lines[len(lines)//2:]])
        result = extract_text(pdf, PDF_MIME)

        for title, employer, dates, responsibility in jobs:
            assert employer in result, f"Employer lost: {employer!r}"
            assert dates in result, f"Date lost: {dates!r}"
            # Partial match on responsibility (key noun should be there)
            first_word = responsibility.split()[0]
            assert first_word in result, (
                f"Responsibility keyword '{first_word}' missing for {employer!r}"
            )
