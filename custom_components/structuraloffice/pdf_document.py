"""PDF document generation for StructuralOffice payment reminders."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .accounting import DIRECTION_RECEIVABLE, cents_to_amount
from .models import StructuralOfficeValidationError

DOCUMENT_TITLES = {
    "payment_reminder": "Zahlungserinnerung",
    "dunning_1": "1. Mahnung",
    "dunning_2": "2. Mahnung",
    "dunning_3": "3. Mahnung",
}


def _page(canvas, document) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D7E3E1"))
    canvas.line(20 * mm, 17 * mm, 190 * mm, 17 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawString(20 * mm, 11 * mm, "Erstellt mit StructuralOffice")
    canvas.drawRightString(190 * mm, 11 * mm, f"Seite {document.page}")
    canvas.restoreState()


def build_invoice_pdf(
    invoice: dict[str, Any],
    company: dict[str, str],
    document_type: str,
    document_date: date | None = None,
) -> bytes:
    """Build a payment reminder or dunning PDF."""
    if invoice["direction"] != DIRECTION_RECEIVABLE:
        raise StructuralOfficeValidationError(
            "Mahndokumente können nur für Ausgangsrechnungen erstellt werden"
        )
    if document_type not in DOCUMENT_TITLES:
        raise StructuralOfficeValidationError("Unbekannter Dokumenttyp")
    document_date = document_date or date.today()
    company_name = company.get("name", "").strip() or "StructuralOffice"
    company_address = company.get("address", "").strip()
    company_email = company.get("email", "").strip()

    buffer = BytesIO()
    document = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=24 * mm,
        title=f"{DOCUMENT_TITLES[document_type]} {invoice['invoice_number']}",
        author=company_name,
    )
    frame = Frame(
        document.leftMargin,
        document.bottomMargin,
        document.width,
        document.height,
        id="main",
    )
    document.addPageTemplates([PageTemplate(id="letter", frames=[frame], onPage=_page)])

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="Company",
            parent=styles["Heading1"],
            textColor=colors.HexColor("#0F766E"),
            fontSize=18,
            leading=22,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Right",
            parent=styles["Normal"],
            alignment=TA_RIGHT,
            textColor=colors.HexColor("#475569"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="DocumentTitle",
            parent=styles["Heading1"],
            textColor=colors.HexColor("#0F172A"),
            fontSize=16,
            spaceAfter=8 * mm,
        )
    )
    normal = styles["BodyText"]
    normal.fontSize = 10.5
    normal.leading = 15

    sender_lines = [company_name, company_address.replace("\n", "<br/>"), company_email]
    sender = "<br/>".join(line for line in sender_lines if line)
    recipient = invoice["contact"]
    if invoice.get("contact_address"):
        recipient += "<br/>" + invoice["contact_address"].replace("\n", "<br/>")

    story = [
        Table(
            [[Paragraph(company_name, styles["Company"]), Paragraph(sender, styles["Right"])]],
            colWidths=[85 * mm, 85 * mm],
        ),
        Spacer(1, 18 * mm),
        Paragraph(recipient, normal),
        Spacer(1, 18 * mm),
        Paragraph(
            f"{DOCUMENT_TITLES[document_type]} zur Rechnung {invoice['invoice_number']}",
            styles["DocumentTitle"],
        ),
        Paragraph(f"Datum: {document_date.strftime('%d.%m.%Y')}", normal),
        Spacer(1, 7 * mm),
        Paragraph("Guten Tag,", normal),
        Spacer(1, 4 * mm),
    ]
    if document_type == "payment_reminder":
        body = (
            "nach unseren Unterlagen ist die folgende Rechnung noch offen. "
            "Möglicherweise hat sich Ihre Zahlung mit diesem Schreiben überschnitten. "
            "Bitte prüfen Sie den Vorgang."
        )
    else:
        body = (
            "trotz Fälligkeit konnten wir für die folgende Rechnung noch keinen "
            "Zahlungseingang feststellen. Bitte begleichen Sie den offenen Betrag "
            "zeitnah oder nehmen Sie Kontakt mit uns auf."
        )
    story.extend([Paragraph(body, normal), Spacer(1, 7 * mm)])

    details = [
        ["Rechnungsnummer", invoice["invoice_number"]],
        ["Rechnungsdatum", date.fromisoformat(invoice["invoice_date"]).strftime("%d.%m.%Y")],
        ["Ursprünglich fällig", date.fromisoformat(invoice["due_date"]).strftime("%d.%m.%Y")],
        ["Offener Betrag", f"{cents_to_amount(invoice['gross_cents'])} {invoice['currency']}"],
    ]
    detail_table = Table(details, colWidths=[55 * mm, 105 * mm])
    detail_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#CCFBF1")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#115E59")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend(
        [
            detail_table,
            Spacer(1, 10 * mm),
            Paragraph("Vielen Dank.", normal),
            Spacer(1, 8 * mm),
            Paragraph(f"Freundliche Grüße<br/>{company_name}", normal),
            Spacer(1, 14 * mm),
            Paragraph(
                "Hinweis: Dieses Dokument wurde automatisch als Organisationshilfe erstellt. "
                "Bitte prüfen Sie Inhalt, Fristen und rechtliche Anforderungen vor dem Versand.",
                ParagraphStyle(
                    name="Disclaimer",
                    parent=normal,
                    fontSize=8,
                    leading=11,
                    textColor=colors.HexColor("#64748B"),
                ),
            ),
        ]
    )
    document.build(story)
    return buffer.getvalue()

