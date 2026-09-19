"""
PCT CRM — shared company/member logo loading for PDF headers.

Two independent logo slots, both optional:
  - logoFile        the company's own logo (Settings → Logo)
  - memberLogoFile   an association/certification badge, e.g. the IPCA
                      member logo (Settings → Member Logo)

Every PDF builder places the company logo on the left and the member
logo on the right of its header — this module is what actually reads
the uploaded files and returns ready-to-place ReportLab Image flowables,
so that layout logic isn't duplicated four times.
"""
from reportlab.platypus import Image
from reportlab.lib.units import mm

from config import LOGOS_DIR


def _load_image(filename, max_height_mm, max_width_mm):
    if not filename:
        return None
    path = LOGOS_DIR / filename
    if not path.exists():
        return None
    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as im:
            w, h = im.size
        ratio = w / h if h else 1
        height = max_height_mm * mm
        width = height * ratio
        if width > max_width_mm * mm:
            width = max_width_mm * mm
            height = width / ratio if ratio else height
        return Image(str(path), width=width, height=height)
    except Exception:
        return None


def load_company_logo(company: dict, max_height_mm=20, max_width_mm=32):
    return _load_image(company.get("logoFile"), max_height_mm, max_width_mm)


def load_member_logo(company: dict, max_height_mm=16, max_width_mm=24):
    """Smaller by default — a membership badge belongs beside the company
    logo, not competing with it for attention."""
    return _load_image(company.get("memberLogoFile"), max_height_mm, max_width_mm)


def build_logo_header_row(company: dict, center_content, total_width,
                           logo_col_width=34 * mm, member_col_width=28 * mm,
                           logo_kwargs=None, member_kwargs=None):
    """A Table with the company logo on the left, `center_content` (a
    Paragraph or similar flowable) in the middle, and the member logo on
    the right — gracefully collapsing to fewer columns when one or both
    logos aren't uploaded, so callers never need their own branch for
    "what if there's no logo".
    """
    from reportlab.platypus import Table, TableStyle

    logo_kwargs = logo_kwargs or {}
    member_kwargs = member_kwargs or {}
    company_logo = load_company_logo(company, **logo_kwargs)
    member_logo = load_member_logo(company, **member_kwargs)

    if company_logo and member_logo:
        center_width = total_width - logo_col_width - member_col_width
        row = [company_logo, center_content, member_logo]
        widths = [logo_col_width, center_width, member_col_width]
        extra_style = [("ALIGN", (2, 0), (2, 0), "RIGHT")]
    elif company_logo:
        center_width = total_width - logo_col_width
        row = [company_logo, center_content]
        widths = [logo_col_width, center_width]
        extra_style = []
    elif member_logo:
        center_width = total_width - member_col_width
        row = [center_content, member_logo]
        widths = [center_width, member_col_width]
        extra_style = [("ALIGN", (1, 0), (1, 0), "RIGHT")]
    else:
        row = [center_content]
        widths = [total_width]
        extra_style = []

    tbl = Table([row], colWidths=widths)
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (-1, 0), (-1, 0), 0),
    ] + extra_style))
    return tbl
