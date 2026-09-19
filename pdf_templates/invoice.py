"""
PCT CRM — GST-compliant invoice PDF, built server-side with ReportLab.
No browser print() involved — returns an in-memory PDF buffer.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                 Spacer, HRFlowable, Image)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT

from utils import amount_in_words, fmt_currency
from pdf_templates.fonts import ensure_fonts_registered, FONT_REGULAR, FONT_BOLD
from pdf_templates.branding import load_company_logo, load_member_logo, build_logo_header_row

ACCENT = colors.HexColor("#00a868")
NAVY = colors.HexColor("#0a0f1e")
MUTED = colors.HexColor("#64748b")
BORDER = colors.HexColor("#e1e7ef")


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("CompanyName", parent=ss["Heading1"], fontSize=14,
                           textColor=NAVY, spaceAfter=0, leading=17,
                           fontName=FONT_BOLD))
    ss.add(ParagraphStyle("Tagline", parent=ss["Normal"], fontSize=8,
                           textColor=MUTED, spaceAfter=0, fontName=FONT_REGULAR))
    ss.add(ParagraphStyle("Small", parent=ss["Normal"], fontSize=8.5,
                           textColor=NAVY, leading=13, fontName=FONT_REGULAR))
    ss.add(ParagraphStyle("SmallMuted", parent=ss["Normal"], fontSize=8.5,
                           textColor=MUTED, leading=12, fontName=FONT_REGULAR))
    ss.add(ParagraphStyle("DocTitleBig", parent=ss["Normal"], fontSize=22,
                           textColor=NAVY, leading=26, fontName=FONT_BOLD))
    ss.add(ParagraphStyle("InvoiceTitle", parent=ss["Normal"], fontSize=18,
                           textColor=ACCENT, alignment=TA_RIGHT, leading=20,
                           fontName=FONT_BOLD))
    ss.add(ParagraphStyle("RightSmall", parent=ss["Small"], alignment=TA_RIGHT))
    ss.add(ParagraphStyle("CenterSmall", parent=ss["Small"], alignment=TA_CENTER))
    ss.add(ParagraphStyle("SectionLabel", parent=ss["Normal"], fontSize=8,
                           textColor=MUTED, spaceAfter=2, fontName=FONT_REGULAR))
    return ss


def build_invoice_pdf(invoice, items, company: dict) -> io.BytesIO:
    ensure_fonts_registered()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             topMargin=16 * mm, bottomMargin=16 * mm,
                             leftMargin=16 * mm, rightMargin=16 * mm,
                             title=f"Invoice {invoice['inv_number']}")
    ss = _styles()
    story = []

    is_duplicate = False  # single canonical copy; label kept simple
    tag = "DUPLICATE" if is_duplicate else "ORIGINAL FOR RECIPIENT"

    # ---- "INVOICE" header bar -------------------------------------------
    title_tbl = Table([[
        Paragraph("INVOICE", ss["DocTitleBig"]),
        Paragraph(tag, ss["RightSmall"]),
    ]], colWidths=[110 * mm, 64 * mm])
    title_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(title_tbl)
    story.append(HRFlowable(width="100%", thickness=1.6, color=ACCENT))
    story.append(Spacer(1, 8))

    # ---- Company branding block: logo (if any) + name/contact/address ----
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
        company_lines.append(
            f"<font size=7.5 color='#64748b'>{company['companyAddress']}</font>")
    if contact_bits:
        company_lines.append(
            f"<font size=7.5 color='#64748b'>{' &nbsp;|&nbsp; '.join(contact_bits)}</font>")
    if company.get("gstNumber"):
        company_lines.append(
            f"<font size=7.5 color='#64748b'>GSTIN: {company['gstNumber']}</font>")

    company_para = Paragraph("<br/>".join(company_lines), ss["Small"])
    brand_tbl = build_logo_header_row(company, company_para, 174 * mm)
    story.append(brand_tbl)
    story.append(Spacer(1, 10))

    meta_rows = [
        ["Invoice No.", invoice["inv_number"]],
        ["Invoice Date", invoice["invoice_date"]],
        ["Due Date", invoice["due_date"] or "—"],
        ["Service Period", invoice["service_period"] or "—"],
        ["Work Order No.", invoice["work_order_no"] or "—"],
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

    # Company identity already appears in the branding block above, so this
    # section only needs the recipient and the invoice's own metadata.
    from_to = [[
        Paragraph("<b>Bill To</b>", ss["SectionLabel"]),
        "",
    ], [
        Paragraph(
            f"<b>{invoice['client_name']}</b><br/>"
            f"{(invoice['client_address'] or '').replace(chr(10), '<br/>')}<br/>"
            f"Phone: {invoice['client_phone'] or '—'}<br/>"
            + (f"GSTIN: {invoice['client_gst']}" if invoice["client_gst"] else "GSTIN: Unregistered"),
            ss["Small"],
        ),
        meta_tbl,
    ]]
    ft_tbl = Table(from_to, colWidths=[87 * mm, 87 * mm])
    ft_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(ft_tbl)
    story.append(Spacer(1, 12))

    # ---- Line items table -------------------------------------------------
    table_head = ["#", "Service / Description", "HSN/SAC", "Qty", "Rate (₹)", "Amount (₹)"]
    rows = [table_head]
    for i, it in enumerate(items, start=1):
        desc = it["service"]
        if it["description"]:
            desc += f"<br/><font size=7.5 color='#64748b'>{it['description']}</font>"
        rows.append([
            str(i), Paragraph(desc, ss["Small"]), it["hsn_sac"] or "",
            f"{it['quantity']:g}", fmt_currency(it["unit_price"]).replace("₹", ""),
            fmt_currency(it["amount"]).replace("₹", ""),
        ])

    items_tbl = Table(rows, colWidths=[8 * mm, 74 * mm, 22 * mm, 14 * mm, 26 * mm, 30 * mm],
                       repeatRows=1)
    items_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fc")]),
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 8))

    # ---- Totals -------------------------------------------------------------
    totals_rows = [
        ["Subtotal", fmt_currency(invoice["subtotal"])],
        [f"CGST @ {invoice['cgst_rate']:g}%", fmt_currency(invoice["cgst_amount"])],
        [f"SGST @ {invoice['sgst_rate']:g}%", fmt_currency(invoice["sgst_amount"])],
        ["Total", fmt_currency(invoice["total_amount"])],
    ]
    totals_tbl = Table(totals_rows, colWidths=[40 * mm, 34 * mm])
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
        Paragraph(f"<b>Amount in words:</b><br/>{amount_in_words(invoice['total_amount'])}",
                  ss["SmallMuted"]),
        totals_tbl,
    ]], colWidths=[100 * mm, 74 * mm])
    wrapper.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(wrapper)
    story.append(Spacer(1, 10))

    payment_line = (f"Amount Paid: {fmt_currency(invoice['amount_paid'])}   |   "
                     f"Balance Due: {fmt_currency(invoice['balance_due'])}   |   "
                     f"Status: {invoice['payment_status']}")
    story.append(Paragraph(f"<b>{payment_line}</b>", ss["Small"]))
    story.append(Spacer(1, 14))

    if company.get("bankDetails"):
        story.append(Paragraph("<b>Bank Details</b>", ss["SectionLabel"]))
        story.append(Paragraph(company["bankDetails"].replace("\n", "<br/>"), ss["SmallMuted"]))
        story.append(Spacer(1, 10))

    if invoice["notes"]:
        story.append(Paragraph("<b>Notes</b>", ss["SectionLabel"]))
        story.append(Paragraph(invoice["notes"], ss["SmallMuted"]))
        story.append(Spacer(1, 10))

    story.append(Spacer(1, 20))
    sig_tbl = Table([[
        Paragraph("Customer Signature", ss["CenterSmall"]),
        Paragraph(f"For {company.get('companyName','')}<br/><br/><br/>Authorised Signatory",
                  ss["CenterSmall"]),
    ]], colWidths=[87 * mm, 87 * mm])
    sig_tbl.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (0, 0), 0.5, BORDER),
        ("LINEABOVE", (1, 0), (1, 0), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(sig_tbl)
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
    story.append(Spacer(1, 4))
    story.append(Paragraph(company.get("invoiceFooter", "Thank you for your business"),
                            ss["CenterSmall"]))

    doc.build(story)
    buf.seek(0)
    return buf
