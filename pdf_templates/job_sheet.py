"""
PCT CRM — printable Job Sheet: a compact 8" x 5" card that travels with a
client's file, letting the field operator log the date, their name, what
was done, and get a signature at each of up to 4 visits. Distinct from the
detailed digital job_cards record (chemicals, GPS, photos) — this is the
simple paper-style companion sheet for a physical file or clipboard.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                 Spacer, HRFlowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

from pdf_templates.fonts import ensure_fonts_registered, FONT_REGULAR, FONT_BOLD
from pdf_templates.branding import build_logo_header_row

NAVY = colors.HexColor("#0a0f1e")
MUTED = colors.HexColor("#64748b")
BORDER = colors.HexColor("#9aa5b8")

PAGE_WIDTH = 8 * inch
PAGE_HEIGHT = 5 * inch


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("JSCompanyName", parent=ss["Heading1"], fontSize=13,
                           textColor=NAVY, leading=15, spaceAfter=0, fontName=FONT_BOLD))
    ss.add(ParagraphStyle("JSSmall", parent=ss["Normal"], fontSize=7,
                           textColor=MUTED, leading=9, fontName=FONT_REGULAR))
    ss.add(ParagraphStyle("JSClient", parent=ss["Normal"], fontSize=9,
                           textColor=NAVY, leading=12, fontName=FONT_REGULAR))
    ss.add(ParagraphStyle("JSTableHead", parent=ss["Normal"], fontSize=8.5,
                           textColor=colors.white, alignment=TA_CENTER,
                           fontName=FONT_BOLD))
    return ss


def build_job_sheet_pdf(client, visits, company: dict) -> io.BytesIO:
    """`visits` is a list of up to 4 row objects (scheduled_date,
    tech_name) for this client, most relevant first — upcoming/recent
    scheduled visits are used to pre-fill the Date/Operator columns where
    available; any remaining rows are left blank for the operator to fill
    in by hand. Details/Remarks/Sign are always left blank — they're
    completed on-site, not known in advance."""
    buf = io.BytesIO()
    ensure_fonts_registered()
    doc = SimpleDocTemplate(
        buf, pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
        topMargin=0.25 * inch, bottomMargin=0.2 * inch,
        leftMargin=0.3 * inch, rightMargin=0.3 * inch,
        title=f"Job Sheet — {client['name']}",
    )
    ss = _styles()
    story = []

    # ---- Company header ----------------------------------------------------
    contact_bits = []
    if company.get("companyPhone"):
        phones = company["companyPhone"]
        if company.get("companyPhone2"):
            phones += f" / {company['companyPhone2']}"
        contact_bits.append(f"Ph: {phones}")
    if company.get("companyEmail"):
        contact_bits.append(company["companyEmail"])
    if company.get("companyWebsite"):
        contact_bits.append(company["companyWebsite"])

    company_bits = [Paragraph(company.get("companyName", ""), ss["JSCompanyName"])]
    if company.get("companyAddress"):
        company_bits.append(Paragraph(company["companyAddress"], ss["JSSmall"]))
    if contact_bits:
        company_bits.append(Paragraph(" | ".join(contact_bits), ss["JSSmall"]))

    # Wrap the stacked name/address/contact lines in a single-cell table so
    # build_logo_header_row can treat them as one "center content" flowable.
    company_block = Table([[b] for b in company_bits], colWidths=[None])
    company_block.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 1), (-1, -1), 1),
    ]))

    usable_width = PAGE_WIDTH - 0.6 * inch
    brand_row = build_logo_header_row(
        company, company_block, usable_width,
        logo_col_width=0.7 * inch, member_col_width=0.55 * inch,
        logo_kwargs={"max_height_mm": 12, "max_width_mm": 18},
        member_kwargs={"max_height_mm": 10, "max_width_mm": 14},
    )
    story.append(brand_row)
    story.append(Spacer(1, 5))
    story.append(HRFlowable(width="100%", thickness=1, color=NAVY))
    story.append(Spacer(1, 5))

    # ---- Client details line -------------------------------------------------
    client_bits = [f"<b>Client:</b> {client['name']}"]
    if client["area"] or client["address"]:
        client_bits.append(client["address"] or client["area"])
    if client["phone"]:
        client_bits.append(f"Ph: {client['phone']}")
    story.append(Paragraph("  &nbsp;|&nbsp;  ".join(client_bits), ss["JSClient"]))
    story.append(Spacer(1, 8))

    # ---- 4-column x 5-row visit log table ------------------------------------
    header = [
        Paragraph("Date", ss["JSTableHead"]),
        Paragraph("Operator", ss["JSTableHead"]),
        Paragraph("Details", ss["JSTableHead"]),
        Paragraph("Remarks / Sign", ss["JSTableHead"]),
    ]
    rows = [header]
    for i in range(4):
        if i < len(visits):
            date_cell = visits[i]["scheduled_date"] or ""
            operator_cell = visits[i]["tech_name"] or ""
        else:
            date_cell = ""
            operator_cell = ""
        rows.append([date_cell, operator_cell, "", ""])

    col_widths = [1.1 * inch, 1.3 * inch, 2.6 * inch, 2.0 * inch]
    row_heights = [0.28 * inch] + [0.55 * inch] * 4
    table = Table(rows, colWidths=col_widths, rowHeights=row_heights)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.75, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 1), (1, -1), "CENTER"),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)

    doc.build(story)
    buf.seek(0)
    return buf
