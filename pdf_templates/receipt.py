import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER

from utils import amount_in_words, fmt_currency
from pdf_templates.fonts import ensure_fonts_registered, FONT_REGULAR, FONT_BOLD
from pdf_templates.branding import build_logo_header_row

ACCENT = colors.HexColor("#00a868")
NAVY = colors.HexColor("#0a0f1e")
MUTED = colors.HexColor("#64748b")


def build_receipt_pdf(payment, company: dict) -> io.BytesIO:
    ensure_fonts_registered()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A5, topMargin=14 * mm, bottomMargin=14 * mm,
                             leftMargin=14 * mm, rightMargin=14 * mm,
                             title="Payment Receipt")
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("ReceiptTitle", parent=ss["Heading1"], fontSize=13,
                           textColor=NAVY, fontName=FONT_BOLD, leading=15))
    ss.add(ParagraphStyle("Accent", parent=ss["Normal"], fontSize=13, textColor=ACCENT,
                           alignment=TA_RIGHT, fontName=FONT_BOLD))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], fontSize=9.5, leading=14,
                           fontName=FONT_REGULAR))
    ss.add(ParagraphStyle("Muted", parent=ss["Normal"], fontSize=8.5, textColor=MUTED,
                           fontName=FONT_REGULAR))
    ss.add(ParagraphStyle("Center", parent=ss["Normal"], fontSize=8.5, alignment=TA_CENTER,
                           textColor=MUTED, fontName=FONT_REGULAR))

    title_row = Table([[Paragraph(f"<b>{company.get('companyName','')}</b>", ss["ReceiptTitle"]),
                         Paragraph("RECEIPT", ss["Accent"])]], colWidths=[85 * mm, 35 * mm])
    company_block = Paragraph(company.get("companyAddress", ""), ss["Muted"])
    brand_row = build_logo_header_row(
        company, company_block, 120 * mm,
        logo_col_width=20 * mm, member_col_width=16 * mm,
        logo_kwargs={"max_height_mm": 14, "max_width_mm": 20},
        member_kwargs={"max_height_mm": 11, "max_width_mm": 16},
    )

    story = [
        title_row,
        Spacer(1, 4),
        brand_row,
        Spacer(1, 6),
        HRFlowable(width="100%", thickness=1.2, color=ACCENT),
        Spacer(1, 10),
    ]

    rows = [
        ["Received From", payment["client_name"]],
        ["Against Invoice", payment["inv_number"]],
        ["Payment Date", payment["payment_date"]],
        ["Payment Mode", payment["payment_mode"] or "—"],
        ["Reference No.", payment["reference_no"] or "—"],
        ["Received By", payment["received_by"] or "—"],
    ]
    tbl = Table(rows, colWidths=[38 * mm, 92 * mm])
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MUTED))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"<b>Amount Received: {fmt_currency(payment['amount'])}</b>",
                            ss["Body"]))
    story.append(Paragraph(amount_in_words(payment["amount"]), ss["Muted"]))
    if payment["notes"]:
        story.append(Spacer(1, 8))
        story.append(Paragraph(f"Notes: {payment['notes']}", ss["Muted"]))
    story.append(Spacer(1, 24))
    story.append(Paragraph("Authorised Signatory", ss["Muted"]))
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MUTED))
    story.append(Spacer(1, 4))
    story.append(Paragraph("This is a computer-generated receipt.", ss["Center"]))

    doc.build(story)
    buf.seek(0)
    return buf
