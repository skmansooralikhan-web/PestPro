"""
PCT CRM — shared font registration for every PDF builder.

The standard 14 PDF base fonts (Helvetica, Times, Courier) do not include
the Indian Rupee sign (₹, U+20B9) at all — it renders as a broken/missing
glyph box regardless of what's installed on the machine generating the
PDF. DejaVu Sans does include it, so it's bundled here (see fonts/LICENSE.txt)
and registered once, with bold mapped so `<b>` tags inside ReportLab
Paragraphs work correctly.

Every PDF builder should call `ensure_fonts_registered()` before building,
and use "DejaVuSans" / "DejaVuSans-Bold" as the fontName on every style
(not just the ones that show currency) so a document doesn't visually mix
two different typefaces.
"""
import os

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONT_REGULAR = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"

_registered = False


def ensure_fonts_registered():
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, os.path.join(FONT_DIR, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(
        FONT_REGULAR, normal=FONT_REGULAR, bold=FONT_BOLD,
        italic=FONT_REGULAR, boldItalic=FONT_BOLD,
    )
    _registered = True
