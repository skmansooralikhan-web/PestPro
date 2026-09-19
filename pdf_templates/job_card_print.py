"""
PCT CRM — printable Job Card record: the completed visit record (pests
found, treatments done, chemicals used with dilution, observations,
signature) as a two-copy PDF — one page for the customer, an identical
second page for the office file. Distinct from job_sheet.py, which is a
blank pre-visit companion sheet filled in by hand; this prints what was
actually captured digitally through the job card wizard.
"""
import base64
import binascii
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                 Spacer, HRFlowable, Image, PageBreak)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER

from pdf_templates.fonts import ensure_fonts_registered, FONT_REGULAR, FONT_BOLD
from pdf_templates.branding import build_logo_header_row

PINE = colors.HexColor("#0b5c33")
INK = colors.HexColor("#16211c")
MUTED = colors.HexColor("#57645c")
BORDER = colors.HexColor("#c3cbbe")
SAFETY = colors.HexColor("#e2711d")

PAGE_WIDTH, PAGE_HEIGHT = A4
USABLE_WIDTH = PAGE_WIDTH - 32 * mm


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("JCTitle", parent=ss["Heading1"], fontSize=14,
                           textColor=INK, fontName=FONT_BOLD, leading=16))
    ss.add(ParagraphStyle("JCCopyLabel", parent=ss["Normal"], fontSize=10,
                           textColor=SAFETY, fontName=FONT_BOLD, alignment=TA_RIGHT))
    ss.add(ParagraphStyle("JCBody", parent=ss["Normal"], fontSize=9, leading=13,
                           fontName=FONT_REGULAR, textColor=INK))
    ss.add(ParagraphStyle("JCMuted", parent=ss["Normal"], fontSize=8, leading=11,
                           fontName=FONT_REGULAR, textColor=MUTED))
    ss.add(ParagraphStyle("JCSectionHead", parent=ss["Normal"], fontSize=9.5,
                           fontName=FONT_BOLD, textColor=PINE, spaceBefore=8, spaceAfter=3))
    ss.add(ParagraphStyle("JCTableHead", parent=ss["Normal"], fontSize=8,
                           fontName=FONT_BOLD, textColor=colors.white, alignment=TA_CENTER))
    ss.add(ParagraphStyle("JCTableCell", parent=ss["Normal"], fontSize=8.5,
                           fontName=FONT_REGULAR, textColor=INK, leading=11))
    ss.add(ParagraphStyle("JCCenter", parent=ss["Normal"], fontSize=7.5,
                           fontName=FONT_REGULAR, textColor=MUTED, alignment=TA_CENTER))
    return ss


def _decode_signature(data_url, max_width_mm=55, max_height_mm=22):
    """Turns the signature pad's data: URL into a ReportLab Image flowable,
    or None if there's nothing usable to decode (waived, empty, corrupt)."""
    if not data_url or "," not in data_url:
        return None
    try:
        header, encoded = data_url.split(",", 1)
        raw = base64.b64decode(encoded)
        if len(raw) < 100:  # matches the client-side minimum-length guard
            return None
        return Image(io.BytesIO(raw), width=max_width_mm * mm, height=max_height_mm * mm)
    except (ValueError, binascii.Error, OSError):
        return None


def _simple_table(rows, headers, col_widths, ss):
    data = [[Paragraph(h, ss["JCTableHead"]) for h in headers]]
    for row in rows:
        data.append([Paragraph(str(cell) if cell not in (None, "") else "—", ss["JCTableCell"])
                     for cell in row])
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PINE),
        ("GRID", (0, 0), (-1, -1), 0.6, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def _build_copy(jc, pests_found, treatments_done, chemicals_used, followup_visit,
                 company, ss, copy_label):
    story = []

    company_block = Paragraph(company.get("companyAddress", ""), ss["JCMuted"])
    title_row = Table(
        [[Paragraph(company.get("companyName", ""), ss["JCTitle"]),
          Paragraph(copy_label, ss["JCCopyLabel"])]],
        colWidths=[USABLE_WIDTH - 40 * mm, 40 * mm],
    )
    title_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(title_row)
    story.append(Spacer(1, 3))
    brand_row = build_logo_header_row(
        company, company_block, USABLE_WIDTH,
        logo_col_width=20 * mm, member_col_width=16 * mm,
        logo_kwargs={"max_height_mm": 13, "max_width_mm": 19},
        member_kwargs={"max_height_mm": 10, "max_width_mm": 15},
    )
    story.append(brand_row)
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.2, color=PINE))
    story.append(Spacer(1, 8))
    story.append(Paragraph("JOB CARD", ss["JCTitle"]))
    story.append(Spacer(1, 6))

    # ---- Visit summary ---------------------------------------------------
    outcome = jc["visit_outcome"] or "Completed"
    summary_rows = [
        ["Client", jc["client_name"]],
        ["Address", jc["client_address"] or jc["client_area"] or "—"],
        ["Date", (jc["start_time"] or "")[:10] or (jc["scheduled_date"] or "—")],
        ["Technician", jc["tech_name"]],
        ["Outcome", outcome],
    ]
    summary_tbl = Table(summary_rows, colWidths=[32 * mm, USABLE_WIDTH - 32 * mm])
    summary_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), FONT_BOLD),
        ("FONTNAME", (1, 0), (1, -1), FONT_REGULAR),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(summary_tbl)
    story.append(Spacer(1, 4))

    if pests_found:
        story.append(Paragraph("Pests Found", ss["JCSectionHead"]))
        story.append(_simple_table(
            [[p.get("pest"), p.get("severity"), p.get("area")] for p in pests_found],
            ["Pest", "Severity", "Area"],
            [55 * mm, 35 * mm, USABLE_WIDTH - 90 * mm], ss))

    if treatments_done:
        story.append(Paragraph("Treatments Done", ss["JCSectionHead"]))
        story.append(_simple_table(
            [[t.get("service"), t.get("chemical"), t.get("area")] for t in treatments_done],
            ["Service", "Chemical / Method", "Area"],
            [55 * mm, 55 * mm, USABLE_WIDTH - 110 * mm], ss))

    if chemicals_used:
        story.append(Paragraph("Chemicals Used", ss["JCSectionHead"]))
        story.append(_simple_table(
            [[c.get("name"), f"{c.get('qty', '')} {c.get('unit', '')}".strip(), c.get("dilution")]
             for c in chemicals_used],
            ["Chemical", "Quantity", "Dilution / Mix Ratio"],
            [60 * mm, 35 * mm, USABLE_WIDTH - 95 * mm], ss))

    if jc["observations"]:
        story.append(Paragraph("Observations", ss["JCSectionHead"]))
        story.append(Paragraph(jc["observations"], ss["JCBody"]))

    if jc["recommendations"]:
        story.append(Paragraph("Recommendations", ss["JCSectionHead"]))
        story.append(Paragraph(jc["recommendations"], ss["JCBody"]))

    if jc["followup_required"]:
        story.append(Paragraph("Follow-up", ss["JCSectionHead"]))
        followup_line = jc["followup_reason"] or "Follow-up visit required."
        if followup_visit:
            followup_line += f" Scheduled for {followup_visit['scheduled_date']}."
        story.append(Paragraph(followup_line, ss["JCBody"]))

    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
    story.append(Spacer(1, 8))

    sig_image = _decode_signature(jc["client_signature"])
    if sig_image:
        story.append(sig_image)
        story.append(Paragraph("Customer Signature", ss["JCMuted"]))
    elif jc["signature_waived"]:
        story.append(Paragraph(
            f"Customer signature not obtained — {jc['signature_waived_reason'] or 'no reason given'}.",
            ss["JCMuted"]))
    else:
        story.append(Spacer(1, 18))
        story.append(Paragraph("Customer Signature: _____________________________", ss["JCMuted"]))

    story.append(Spacer(1, 10))
    story.append(Paragraph("This is a computer-generated job card record.", ss["JCCenter"]))
    return story


def build_job_card_pdf(jc, pests_found, treatments_done, chemicals_used,
                        followup_visit, company: dict) -> io.BytesIO:
    """Two-page PDF: an identical Customer Copy and Office Copy of the
    completed job card, so both can be printed and handed/filed
    separately. Built as two full pages (rather than squeezed into one
    sheet's top/bottom half) so a job card with a lot of pests,
    treatments, or chemicals logged never risks being cut off."""
    ensure_fonts_registered()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, topMargin=14 * mm, bottomMargin=14 * mm,
        leftMargin=16 * mm, rightMargin=16 * mm,
        title=f"Job Card — {jc['client_name']}",
    )
    ss = _styles()
    story = []
    story += _build_copy(jc, pests_found, treatments_done, chemicals_used,
                          followup_visit, company, ss, "CUSTOMER COPY")
    story.append(PageBreak())
    story += _build_copy(jc, pests_found, treatments_done, chemicals_used,
                          followup_visit, company, ss, "OFFICE COPY")

    doc.build(story)
    buf.seek(0)
    return buf
