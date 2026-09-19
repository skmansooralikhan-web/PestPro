"""
PCT CRM — quotation PDF, built server-side with ReportLab. Structurally
close to the invoice PDF (same layout language) but explicitly labelled
as a quotation, with a validity date instead of a due date, and no
payment-status line.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                 Spacer, HRFlowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT

from utils import amount_in_words, fmt_currency
from pdf_templates.fonts import ensure_fonts_registered, FONT_REGULAR, FONT_BOLD
from pdf_templates.branding import build_logo_header_row

ACCENT = colors.HexColor("#00a868")
NAVY = colors.HexColor("#0a0f1e")
MUTED = colors.HexColor("#64748b")
BORDER = colors.HexColor("#e1e7ef")


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("QCompanyName", parent=ss["Heading1"], fontSize=16,
                           textColor=NAVY, spaceAfter=0, leading=19, fontName=FONT_BOLD))
    ss.add(ParagraphStyle("QTagline", parent=ss["Normal"], fontSize=8,
                           textColor=MUTED, spaceAfter=0, fontName=FONT_REGULAR))
    ss.add(ParagraphStyle("QSmall", parent=ss["Normal"], fontSize=8.5,
                           textColor=NAVY, leading=12, fontName=FONT_REGULAR))
    ss.add(ParagraphStyle("QSmallMuted", parent=ss["Normal"], fontSize=8.5,
                           textColor=MUTED, leading=12, fontName=FONT_REGULAR))
    ss.add(ParagraphStyle("QDocTitle", parent=ss["Normal"], fontSize=22,
                           textColor=NAVY, alignment=TA_LEFT, leading=26,
                           fontName=FONT_BOLD))
    ss.add(ParagraphStyle("QRightSmall", parent=ss["QSmall"], alignment=TA_RIGHT))
    ss.add(ParagraphStyle("QCenterSmall", parent=ss["QSmall"], alignment=TA_CENTER))
    ss.add(ParagraphStyle("QSectionLabel", parent=ss["Normal"], fontSize=8,
                           textColor=MUTED, spaceAfter=2, fontName=FONT_REGULAR))
    return ss


def build_quotation_pdf(quote, items, recipient: dict, company: dict) -> io.BytesIO:
    ensure_fonts_registered()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             topMargin=16 * mm, bottomMargin=16 * mm,
                             leftMargin=16 * mm, rightMargin=16 * mm,
                             title=f"Quotation {quote['quote_number']}")
    ss = _styles()
    story = []

    header_data = [[
        Paragraph("QUOTATION", ss["QDocTitle"]),
        Paragraph("Not a tax invoice", ss["QRightSmall"]),
    ]]
    header_tbl = Table(header_data, colWidths=[110 * mm, 64 * mm])
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(header_tbl)
    story.append(HRFlowable(width="100%", thickness=1.4, color=ACCENT))
    story.append(Spacer(1, 8))

    contact_bits = []
    if company.get("companyPhone"):
        phones = company["companyPhone"]
        if company.get("companyPhone2"):
            phones += f" / {company['companyPhone2']}"
        contact_bits.append(f"Phone: {phones}")
    if company.get("companyEmail"):
        contact_bits.append(f"Email: {company['companyEmail']}")
    if company.get("companyWebsite"):
        contact_bits.append(f"Web: {company['companyWebsite']}")

    company_lines = [f"<b>{company.get('companyName','')}</b>"]
    if company.get("companyAddress"):
        company_lines.append(f"<font size=7.5 color='#64748b'>{company['companyAddress']}</font>")
    if contact_bits:
        company_lines.append(f"<font size=7.5 color='#64748b'>{' &nbsp;|&nbsp; '.join(contact_bits)}</font>")
    if company.get("gstNumber"):
        company_lines.append(f"<font size=7.5 color='#64748b'>GSTIN: {company['gstNumber']}</font>")

    company_para = Paragraph("<br/>".join(company_lines), ss["QSmall"])
    brand_tbl = build_logo_header_row(company, company_para, 174 * mm)
    story.append(brand_tbl)
    story.append(Spacer(1, 8))

    meta_rows = [
        ["Quotation No.", quote["quote_number"]],
        ["Quotation Date", quote["quote_date"]],
        ["Valid Until", quote["valid_until"] or "—"],
    ]
    meta_tbl = Table(meta_rows, colWidths=[30 * mm, 40 * mm])
    meta_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (1, 0), (1, -1), NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))

    recipient_lines = [f"<b>{recipient.get('name','—')}</b>"]
    if recipient.get("address"):
        recipient_lines.append(recipient["address"].replace("\n", "<br/>"))
    recipient_lines.append(f"Phone: {recipient.get('phone','—')}")
    if recipient.get("area"):
        recipient_lines.append(recipient["area"])

    from_to = [[
        Paragraph("<b>Prepared For</b>", ss["QSectionLabel"]),
        "",
    ], [
        Paragraph("<br/>".join(recipient_lines), ss["QSmall"]),
        meta_tbl,
    ]]
    ft_tbl = Table(from_to, colWidths=[87 * mm, 87 * mm])
    ft_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2)]))
    story.append(ft_tbl)
    story.append(Spacer(1, 12))

    table_head = ["#", "Service / Description", "Qty", "Rate (₹)", "Amount (₹)"]
    rows = [table_head]
    for i, it in enumerate(items, start=1):
        desc = it["service"]
        if it["description"]:
            desc += f"<br/><font size=7.5 color='#64748b'>{it['description']}</font>"
        rows.append([
            str(i), Paragraph(desc, ss["QSmall"]), f"{it['quantity']:g}",
            fmt_currency(it["unit_price"]).replace("₹", ""),
            fmt_currency(it["amount"]).replace("₹", ""),
        ])

    items_tbl = Table(rows, colWidths=[8 * mm, 96 * mm, 14 * mm, 26 * mm, 30 * mm], repeatRows=1)
    items_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fc")]),
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 8))

    totals_rows = [
        ["Subtotal", fmt_currency(quote["subtotal"])],
        [f"CGST @ {quote['cgst_rate']:g}%", fmt_currency(quote["cgst_amount"])],
        [f"SGST @ {quote['sgst_rate']:g}%", fmt_currency(quote["sgst_amount"])],
        ["Total (Estimated)", fmt_currency(quote["total_amount"])],
    ]
    totals_tbl = Table(totals_rows, colWidths=[44 * mm, 34 * mm])
    totals_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEABOVE", (0, -1), (-1, -1), 1, NAVY),
        ("FONTNAME", (0, -1), (-1, -1), FONT_BOLD),
        ("FONTSIZE", (0, -1), (-1, -1), 11),
        ("TEXTCOLOR", (0, -1), (-1, -1), ACCENT),
    ]))
    wrapper = Table([[
        Paragraph(f"<b>Amount in words:</b><br/>{amount_in_words(quote['total_amount'])}",
                  ss["QSmallMuted"]),
        totals_tbl,
    ]], colWidths=[100 * mm, 74 * mm])
    wrapper.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(wrapper)
    story.append(Spacer(1, 14))

    if quote["notes"]:
        story.append(Paragraph("<b>Notes</b>", ss["QSectionLabel"]))
        story.append(Paragraph(quote["notes"], ss["QSmallMuted"]))
        story.append(Spacer(1, 10))

    story.append(Paragraph(
        "This quotation is an estimate and does not constitute a tax invoice. "
        f"Prices are valid until {quote['valid_until'] or 'the date shown above'}; "
        "GST will be charged at the prevailing rate at the time of billing.",
        ss["QSmallMuted"]))
    story.append(Spacer(1, 20))

    sig_tbl = Table([[
        Paragraph("Accepted By (Customer Signature)", ss["QCenterSmall"]),
        Paragraph(f"For {company.get('companyName','')}<br/><br/><br/>Authorised Signatory",
                  ss["QCenterSmall"]),
    ]], colWidths=[87 * mm, 87 * mm])
    sig_tbl.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (0, 0), 0.5, BORDER),
        ("LINEABOVE", (1, 0), (1, 0), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(sig_tbl)

    doc.build(story)
    buf.seek(0)
    return buf
