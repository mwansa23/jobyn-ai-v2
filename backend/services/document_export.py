"""Document export service for tailored CVs.

Generates professional, ATS-friendly DOCX and PDF files from the structured
CV content stored in :class:`TailoredCV`.

Uses ``python-docx`` for editable DOCX generation and ``reportlab`` for PDF
generation.  Both formats are built from the same validated structured CV
representation so their content is always identical.

Format decisions:
- A4 page size, 2 cm top/bottom margin, 2.5 cm side margins.
- Single-column layout — no tables, columns, text boxes, icons, or graphics.
- Calibri 11pt body for DOCX; Helvetica 11pt body for PDF.
- Clear section headings (Heading 1 equivalent).
- Reverse-chronological employment history preserved by the LLM output.
- Experience entries: bold role title + company line, then indented bullets.
- No photographs, icons, or decorative elements.
"""

from __future__ import annotations

from html import escape
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)


class DocumentExportError(Exception):
    """Raised when document export fails."""


# ── DOCX generation ──────────────────────────────────────────────────────────


def generate_docx(cv_content: dict[str, Any]) -> bytes:
    """Generate a professional DOCX file from structured CV content.

    Args:
        cv_content: The ``content`` field from a :class:`TailoredCV` record.
            Must contain a ``sections`` list.

    Returns:
        DOCX file bytes.

    Raises:
        DocumentExportError: If generation fails.
    """
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    try:
        doc = Document()

        # ── Page layout ──────────────────────────────────────────────
        for sec in doc.sections:
            sec.page_width = Cm(21.0)
            sec.page_height = Cm(29.7)
            sec.top_margin = Cm(2.0)
            sec.bottom_margin = Cm(2.0)
            sec.left_margin = Cm(2.5)
            sec.right_margin = Cm(2.5)

        # ── Default body style ───────────────────────────────────────
        normal = doc.styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(11)
        normal.paragraph_format.space_after = Pt(2)
        normal.paragraph_format.space_before = Pt(0)

        sections = cv_content.get("sections", [])

        for section_data in sections:
            heading = (section_data.get("heading") or "").strip()
            section_type = section_data.get("type", "other")
            content = (section_data.get("content") or "").strip()
            items = section_data.get("items") or []

            # ── Section heading ──────────────────────────────────────
            if heading:
                h = doc.add_paragraph(heading)
                h.style = doc.styles["Heading 1"]
                # Set heading font directly on the run, not on the shared style
                for run in h.runs:
                    run.font.name = "Calibri"
                    run.font.size = Pt(13)
                    run.font.color.rgb = RGBColor(0x17, 0x17, 0x17)
                    run.bold = True
                h.paragraph_format.space_before = Pt(10)
                h.paragraph_format.space_after = Pt(4)

            # ── Contact section: preserve line breaks ────────────────
            if section_type == "contact":
                for line in content.splitlines():
                    if line.strip():
                        p = doc.add_paragraph(line.strip())
                        p.paragraph_format.space_after = Pt(1)
                # Spacer
                doc.add_paragraph("")
                continue

            # ── Summary: single block paragraph ─────────────────────
            if section_type == "summary":
                if content:
                    p = doc.add_paragraph(content)
                    p.paragraph_format.space_after = Pt(6)
                doc.add_paragraph("")
                continue

            # ── List-based sections ──────────────────────────────────
            if items:
                for item in items:
                    if isinstance(item, str):
                        p = doc.add_paragraph(item.strip(), style="List Bullet")
                        p.paragraph_format.space_after = Pt(2)

                    elif isinstance(item, dict):
                        # Experience / education / certification entry
                        title = (
                            item.get("title")
                            or item.get("role")
                            or item.get("name")
                            or ""
                        ).strip()
                        subtitle = (
                            item.get("company")
                            or item.get("organisation")
                            or item.get("institution")
                            or ""
                        ).strip()
                        date_str = (item.get("dates") or item.get("date") or "").strip()
                        details = item.get("details") or item.get("description") or item.get("bullets") or []

                        # Role/company line (bold title, plain subtitle)
                        if title or subtitle:
                            p = doc.add_paragraph()
                            p.paragraph_format.space_before = Pt(4)
                            p.paragraph_format.space_after = Pt(1)
                            if title:
                                run = p.add_run(title)
                                run.bold = True
                            if subtitle:
                                sep = " — " if title else ""
                                p.add_run(sep + subtitle)
                            if date_str:
                                p.add_run(f"  |  {date_str}")

                        # Detail bullets
                        if isinstance(details, list):
                            for d in details:
                                if isinstance(d, str) and d.strip():
                                    bp = doc.add_paragraph(d.strip(), style="List Bullet")
                                    bp.paragraph_format.space_after = Pt(1)
                        elif isinstance(details, str) and details.strip():
                            bp = doc.add_paragraph(details.strip(), style="List Bullet")
                            bp.paragraph_format.space_after = Pt(1)

                    else:
                        p = doc.add_paragraph(str(item), style="List Bullet")
                        p.paragraph_format.space_after = Pt(2)

            elif content:
                p = doc.add_paragraph(content)
                p.paragraph_format.space_after = Pt(4)

            # Section spacer
            doc.add_paragraph("").paragraph_format.space_after = Pt(2)

        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    except Exception as exc:
        logger.exception("DOCX generation failed")
        raise DocumentExportError(f"Failed to generate DOCX: {exc}") from exc


# ── PDF generation ───────────────────────────────────────────────────────────


def generate_pdf(cv_content: dict[str, Any]) -> bytes:
    """Generate a professional PDF file from structured CV content.

    Uses reportlab to produce an ATS-friendly single-column layout on A4.

    Args:
        cv_content: The ``content`` field from a :class:`TailoredCV` record.

    Returns:
        PDF file bytes.

    Raises:
        DocumentExportError: If generation fails.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.colors import HexColor  # ← fix: use Color object, not string
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        ListFlowable,
        ListItem,
    )

    try:
        buf = io.BytesIO()

        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
            leftMargin=2.5 * cm,
            rightMargin=2.5 * cm,
        )

        styles = getSampleStyleSheet()

        # ── Custom styles ────────────────────────────────────────────
        heading_style = ParagraphStyle(
            "CVHeading",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=13,
            spaceBefore=12,
            spaceAfter=4,
            textColor=HexColor("#171717"),  # ← was bare string; now Color object
        )
        subheading_style = ParagraphStyle(
            "CVSubHeading",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            spaceBefore=4,
            spaceAfter=1,
            textColor=HexColor("#171717"),
        )
        body_style = ParagraphStyle(
            "CVBody",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=11,
            leading=15,
            spaceAfter=4,
            alignment=TA_LEFT,
        )
        bullet_style = ParagraphStyle(
            "CVBullet",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=14,
            leftIndent=14,
            spaceAfter=2,
            alignment=TA_LEFT,
        )

        story: list[Any] = []
        sections = cv_content.get("sections", [])

        for section_data in sections:
            heading = (section_data.get("heading") or "").strip()
            section_type = section_data.get("type", "other")
            content = (section_data.get("content") or "").strip()
            items = section_data.get("items") or []

            # ── Section heading ──────────────────────────────────────
            if heading:
                story.append(Paragraph(escape(heading), heading_style))

            # ── Contact: preserve line breaks ────────────────────────
            if section_type == "contact":
                for line in content.splitlines():
                    if line.strip():
                        story.append(Paragraph(escape(line.strip()), body_style))
                story.append(Spacer(1, 6))
                continue

            # ── Summary ──────────────────────────────────────────────
            if section_type == "summary":
                if content:
                    story.append(Paragraph(escape(content), body_style))
                story.append(Spacer(1, 8))
                continue

            # ── List-based sections ──────────────────────────────────
            if items:
                for item in items:
                    if isinstance(item, str):
                        story.append(Paragraph(f"• {escape(item.strip())}", bullet_style))

                    elif isinstance(item, dict):
                        # Experience / education entry
                        title = (
                            item.get("title")
                            or item.get("role")
                            or item.get("name")
                            or ""
                        ).strip()
                        subtitle = (
                            item.get("company")
                            or item.get("organisation")
                            or item.get("institution")
                            or ""
                        ).strip()
                        date_str = (item.get("dates") or item.get("date") or "").strip()
                        details = (
                            item.get("details")
                            or item.get("description")
                            or item.get("bullets")
                            or []
                        )

                        # Role + company line rendered as a paragraph, not a list item
                        # ← fix: was nested in same ListFlowable, losing hierarchy
                        parts = []
                        if title:
                            parts.append(f"<b>{escape(title)}</b>")
                        if subtitle:
                            parts.append(escape(subtitle))
                        if date_str:
                            parts.append(f"<i>{escape(date_str)}</i>")
                        if parts:
                            story.append(Paragraph(" — ".join(parts), subheading_style))

                        # Detail bullets as a separate list
                        bullet_texts: list[str] = []
                        if isinstance(details, list):
                            bullet_texts = [
                                d for d in details if isinstance(d, str) and d.strip()
                            ]
                        elif isinstance(details, str) and details.strip():
                            bullet_texts = [details.strip()]

                        if bullet_texts:
                            story.append(
                                ListFlowable(
                                    [
                                        ListItem(
                                            Paragraph(escape(d), bullet_style),
                                            leftIndent=14,
                                        )
                                        for d in bullet_texts
                                    ],
                                    bulletType="bullet",
                                    start="•",
                                )
                            )

                    else:
                        story.append(Paragraph(f"• {escape(str(item))}", bullet_style))

            elif content:
                story.append(Paragraph(escape(content), body_style))

            story.append(Spacer(1, 8))

        doc.build(story)
        return buf.getvalue()

    except Exception as exc:
        logger.exception("PDF generation failed")
        raise DocumentExportError(f"Failed to generate PDF: {exc}") from exc


def generate_cover_letter_pdf(cover_letter: str) -> bytes:
    """Generate an A4 PDF from plain-text cover-letter content."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    try:
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
            leftMargin=2.5 * cm,
            rightMargin=2.5 * cm,
        )
        body_style = ParagraphStyle(
            "CoverLetterBody",
            parent=getSampleStyleSheet()["Normal"],
            fontName="Helvetica",
            fontSize=11,
            leading=16,
            spaceAfter=8,
        )
        story: list[Any] = []
        for paragraph in cover_letter.splitlines():
            if paragraph.strip():
                story.append(Paragraph(escape(paragraph), body_style))
            else:
                story.append(Spacer(1, 8))
        doc.build(story)
        return buf.getvalue()
    except Exception as exc:
        logger.exception("Cover-letter PDF generation failed")
        raise DocumentExportError(
            f"Failed to generate cover-letter PDF: {exc}"
        ) from exc


__all__ = [
    "generate_docx",
    "generate_pdf",
    "generate_cover_letter_pdf",
    "DocumentExportError",
]
