"""Document export service for tailored CVs.

Generates professional, ATS-friendly DOCX and PDF files from the structured
CV content stored in :class:`TailoredCV`.

Uses ``python-docx`` for DOCX generation and ``reportlab`` for PDF generation.
Both exports contain the same approved CV content.

Format:
- A4 page size.
- Clear professional headings.
- Readable typography (Helvetica/Arial for PDF, Calibri for DOCX).
- Consistent spacing.
- Simple section structure.
- Professional bullet points.
- No photographs, icons, or graphics.
"""

from __future__ import annotations

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
    from docx.shared import Pt, Cm

    try:
        doc = Document()

        # Page setup: A4
        for section in doc.sections:
            section.page_width = Cm(21.0)
            section.page_height = Cm(29.7)
            section.top_margin = Cm(2.0)
            section.bottom_margin = Cm(2.0)
            section.left_margin = Cm(2.5)
            section.right_margin = Cm(2.5)

        # Default font
        style = doc.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(11)
        style.paragraph_format.space_after = Pt(4)

        sections = cv_content.get("sections", [])
        for section_data in sections:
            heading = section_data.get("heading", "")
            section_type = section_data.get("type", "other")
            content = section_data.get("content", "")
            items = section_data.get("items", [])

            # Add heading (skip if empty)
            if heading:
                h = doc.add_heading(heading, level=1)
                h.style.font.size = Pt(13)

            # Contact section: just add content as paragraphs
            if section_type == "contact":
                if content:
                    doc.add_paragraph(content)
                continue

            # Summary section: single paragraph
            if section_type == "summary":
                if content:
                    p = doc.add_paragraph(content)
                    p.paragraph_format.space_after = Pt(8)
                continue

            # List-based sections (experience, skills, education, etc.)
            if items:
                for item in items:
                    if isinstance(item, str):
                        doc.add_paragraph(item, style="List Bullet")
                    elif isinstance(item, dict):
                        # Item may have title, subtitle, and details
                        title = item.get("title", item.get("role", item.get("name", "")))
                        subtitle = item.get("company", item.get("organisation", item.get("institution", "")))
                        details = item.get("details", item.get("description", item.get("bullets", [])))

                        if title or subtitle:
                            p = doc.add_paragraph()
                            if title:
                                run = p.add_run(title)
                                run.bold = True
                            if subtitle:
                                if title:
                                    p.add_run(" — ")
                                p.add_run(subtitle)
                        if isinstance(details, list):
                            for d in details:
                                if isinstance(d, str):
                                    doc.add_paragraph(d, style="List Bullet")
                        elif isinstance(details, str) and details:
                            doc.add_paragraph(details, style="List Bullet")
                    else:
                        doc.add_paragraph(str(item), style="List Bullet")
            elif content:
                doc.add_paragraph(content)

            # Add spacing after each section
            doc.add_paragraph("")

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

        # Custom styles
        heading_style = ParagraphStyle(
            "CVHeading",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=13,
            spaceBefore=12,
            spaceAfter=6,
            textColor="#171717",
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
            parent=body_style,
            leftIndent=18,
            bulletIndent=6,
            spaceAfter=2,
        )

        story = []
        sections = cv_content.get("sections", [])

        for section_data in sections:
            heading = section_data.get("heading", "")
            section_type = section_data.get("type", "other")
            content = section_data.get("content", "")
            items = section_data.get("items", [])

            if heading:
                story.append(Paragraph(heading, heading_style))

            if section_type == "contact":
                if content:
                    story.append(Paragraph(content, body_style))
                story.append(Spacer(1, 6))
                continue

            if section_type == "summary":
                if content:
                    story.append(Paragraph(content, body_style))
                story.append(Spacer(1, 8))
                continue

            if items:
                list_items = []
                for item in items:
                    if isinstance(item, str):
                        list_items.append(ListItem(Paragraph(item, bullet_style)))
                    elif isinstance(item, dict):
                        title = item.get("title", item.get("role", item.get("name", "")))
                        subtitle = item.get("company", item.get("organisation", item.get("institution", "")))
                        details = item.get("details", item.get("description", item.get("bullets", [])))

                        text_parts = []
                        if title:
                            text_parts.append(f"<b>{title}</b>")
                        if subtitle:
                            text_parts.append(subtitle)
                        if text_parts:
                            list_items.append(ListItem(Paragraph(" — ".join(text_parts), bullet_style)))

                        if isinstance(details, list):
                            for d in details:
                                if isinstance(d, str):
                                    list_items.append(ListItem(Paragraph(d, bullet_style)))
                        elif isinstance(details, str) and details:
                            list_items.append(ListItem(Paragraph(details, bullet_style)))
                    else:
                        list_items.append(ListItem(Paragraph(str(item), bullet_style)))

                if list_items:
                    story.append(ListFlowable(list_items, bulletType="bullet", start="•"))
            elif content:
                story.append(Paragraph(content, body_style))

            story.append(Spacer(1, 8))

        doc.build(story)
        return buf.getvalue()

    except Exception as exc:
        logger.exception("PDF generation failed")
        raise DocumentExportError(f"Failed to generate PDF: {exc}") from exc


__all__ = ["generate_docx", "generate_pdf", "DocumentExportError"]
