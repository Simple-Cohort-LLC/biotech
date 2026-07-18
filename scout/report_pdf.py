"""The weekly PDF: one card per company, links live, sorted by score."""

from __future__ import annotations

import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .models import Company

# ---------------------------------------------------------------- palette

PAPER = colors.HexColor("#FAF9F5")
INK = colors.HexColor("#1C2420")
MUTED = colors.HexColor("#68746E")
FAINT = colors.HexColor("#A9B3AD")
RULE = colors.HexColor("#E2E0D8")
ACCENT = colors.HexColor("#0E6E5C")
ACCENT_DARK = colors.HexColor("#0A5245")

ACCENT_HEX = "#0E6E5C"
MUTED_HEX = "#68746E"
FAINT_HEX = "#A9B3AD"

SECTOR_LABELS = {
    "therapeutics": "Therapeutics",
    "diagnostics": "Diagnostics",
    "tools-and-instruments": "Tools & Instruments",
    "synthetic-biology": "Synthetic Biology",
    "techbio-and-bioinformatics": "TechBio & Bioinformatics",
    "ag-and-food-tech": "Ag & Food Tech",
    "industrial-biotech": "Industrial Biotech",
    "medtech-and-devices": "MedTech & Devices",
    "digital-health": "Digital Health",
    "unknown": "Unclassified",
}

# ---------------------------------------------------------------- fonts
#
# Avenir Next (display) + Charter (body) ship with macOS; if either is
# missing — a Linux cron box, say — everything degrades to Helvetica and
# the layout still holds.

_FONT_SOURCES = {
    "Scout-Heavy": ("/System/Library/Fonts/Avenir Next.ttc", 0),  # Bold
    "Scout-Display": ("/System/Library/Fonts/Avenir Next.ttc", 2),  # Demi Bold
    "Scout-Sans": ("/System/Library/Fonts/Avenir Next.ttc", 7),  # Regular
    "Scout-Sans-Medium": ("/System/Library/Fonts/Avenir Next.ttc", 5),  # Medium
    "Scout-Serif": ("/System/Library/Fonts/Supplemental/Charter.ttc", 0),
    "Scout-Serif-Italic": ("/System/Library/Fonts/Supplemental/Charter.ttc", 1),
    "Scout-Serif-Bold": ("/System/Library/Fonts/Supplemental/Charter.ttc", 3),
}

_FONT_FALLBACKS = {
    "Scout-Heavy": "Helvetica-Bold",
    "Scout-Display": "Helvetica-Bold",
    "Scout-Sans": "Helvetica",
    "Scout-Sans-Medium": "Helvetica",
    "Scout-Serif": "Helvetica",
    "Scout-Serif-Italic": "Helvetica-Oblique",
    "Scout-Serif-Bold": "Helvetica-Bold",
}


def _register_fonts() -> tuple[dict[str, str], bool]:
    fonts: dict[str, str] = {}
    all_loaded = True
    for name, (path, index) in _FONT_SOURCES.items():
        try:
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=index))
            fonts[name] = name
        except Exception:
            fonts[name] = _FONT_FALLBACKS[name]
            all_loaded = False
    if all_loaded:
        pdfmetrics.registerFontFamily(
            "Scout-Serif",
            normal="Scout-Serif",
            bold="Scout-Serif-Bold",
            italic="Scout-Serif-Italic",
            boldItalic="Scout-Serif-Bold",
        )
    return fonts, all_loaded


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _tracked(text: str, spaced_fonts: bool) -> str:
    """Letter-space an overline label: single space between letters, a wider
    gap between words so the tracking doesn't run the words together."""
    if not spaced_fonts:
        return text
    # Regular spaces between letters (one space of tracking); non-breaking
    # spaces between words, because Paragraph collapses runs of plain spaces.
    return "\u00a0\u00a0".join(" ".join(word) for word in text.split(" "))


def _fmt_usd(amount: int) -> str:
    if amount >= 1_000_000:
        value = amount / 1_000_000
        return f"${value:.1f}M".replace(".0M", "M")
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}k"
    return f"${amount}"


# ---------------------------------------------------------------- flowables


class _ScorePill(Flowable):
    """Rounded badge with the signal score, right-aligned in the name row."""

    def __init__(self, score: int, font: str):
        super().__init__()
        self.text = f"{score}"
        self.font = font
        self.size = 9
        pad = 7
        self.width = max(stringWidth(self.text, font, self.size) + 2 * pad, 24)
        self.height = 15

    def draw(self) -> None:
        c = self.canv
        c.saveState()
        c.setFillColor(ACCENT)
        c.roundRect(0, 0, self.width, self.height, self.height / 2, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont(self.font, self.size)
        c.drawCentredString(self.width / 2, (self.height - self.size) / 2 + 1.2, self.text)
        c.restoreState()


class _SectionMark(Flowable):
    """Short bold accent underline beneath a section title."""

    def __init__(self, width: float = 26, thickness: float = 2.5):
        super().__init__()
        self.width = width
        self.height = thickness

    def draw(self) -> None:
        self.canv.setFillColor(ACCENT)
        self.canv.rect(0, 0, self.width, self.height, stroke=0, fill=1)


# ---------------------------------------------------------------- styles


def _styles(fonts: dict[str, str], spaced: bool) -> dict[str, ParagraphStyle]:
    return {
        "overline": ParagraphStyle(
            "overline", fontName=fonts["Scout-Sans-Medium"], fontSize=8,
            leading=11, textColor=ACCENT, spaceAfter=7,
        ),
        "title": ParagraphStyle(
            "title", fontName=fonts["Scout-Heavy"], fontSize=27, leading=31,
            textColor=INK, alignment=TA_LEFT, spaceAfter=5,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName=fonts["Scout-Serif-Italic"], fontSize=10.5,
            leading=15, textColor=MUTED, spaceAfter=14,
        ),
        "stat_number": ParagraphStyle(
            "stat_number", fontName=fonts["Scout-Heavy"], fontSize=17,
            leading=20, textColor=INK,
        ),
        "stat_text": ParagraphStyle(
            "stat_text", fontName=fonts["Scout-Display"], fontSize=12,
            leading=15, textColor=INK, spaceBefore=2,
        ),
        "stat_label": ParagraphStyle(
            "stat_label", fontName=fonts["Scout-Sans-Medium"], fontSize=6.5,
            leading=9, textColor=MUTED, spaceBefore=2,
        ),
        "sources_line": ParagraphStyle(
            "sources_line", fontName=fonts["Scout-Sans"], fontSize=7.5,
            leading=11, textColor=FAINT, spaceBefore=8,
        ),
        "section": ParagraphStyle(
            "section", fontName=fonts["Scout-Display"], fontSize=14, leading=17,
            textColor=INK, spaceBefore=18, spaceAfter=4,
        ),
        "company": ParagraphStyle(
            "company", fontName=fonts["Scout-Display"], fontSize=12, leading=15,
            textColor=INK,
        ),
        "meta": ParagraphStyle(
            "meta", fontName=fonts["Scout-Sans"], fontSize=7.5, leading=11,
            textColor=MUTED, spaceBefore=2, spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "body", fontName=fonts["Scout-Serif"], fontSize=9.5, leading=14,
            textColor=INK, spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "small", fontName=fonts["Scout-Serif"], fontSize=8.5, leading=12.5,
            textColor=MUTED, spaceAfter=3,
        ),
        "link": ParagraphStyle(
            "link", fontName=fonts["Scout-Sans"], fontSize=8, leading=12,
            textColor=ACCENT_DARK,
        ),
        "footnote": ParagraphStyle(
            "footnote", fontName=fonts["Scout-Serif-Italic"], fontSize=9,
            leading=13.5, textColor=MUTED, spaceAfter=6,
        ),
    }


# ---------------------------------------------------------------- page chrome


def _make_page_painter(fonts: dict[str, str], spaced: bool, today: date):
    footer_font = fonts["Scout-Sans"]
    label = _tracked("BIOTECH SCOUT", spaced)

    def paint(canvas, doc) -> None:
        width, height = LETTER
        canvas.saveState()
        # Paper background, then the accent band along the top edge.
        canvas.setFillColor(PAPER)
        canvas.rect(0, 0, width, height, stroke=0, fill=1)
        canvas.setFillColor(ACCENT)
        canvas.rect(0, height - 5, width, 5, stroke=0, fill=1)
        canvas.setFillColor(ACCENT_DARK)
        canvas.rect(0, height - 5, 1.6 * inch, 5, stroke=0, fill=1)
        # Footer: hairline, running title left, page number right.
        y = 0.55 * inch
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(doc.leftMargin, y, width - doc.rightMargin, y)
        canvas.setFont(footer_font, 7)
        canvas.setFillColor(FAINT)
        canvas.drawString(
            doc.leftMargin, y - 12, f"{label}  ·  {today.strftime('%B %d, %Y')}"
        )
        canvas.drawRightString(width - doc.rightMargin, y - 12, f"{canvas.getPageNumber()}")
        canvas.restoreState()

    return paint


# ---------------------------------------------------------------- content


def _stat_band(
    companies: list[Company],
    source_counts: dict[str, int],
    by_sector: dict[str, list[Company]],
    styles: dict,
    spaced: bool,
    content_width: float,
) -> Table:
    total_signals = sum(len(c.signals) for c in companies)
    top_sector = max(by_sector, key=lambda s: len(by_sector[s])) if by_sector else "unknown"
    tiles = [
        (f"{len(companies)}", "COMPANIES SURFACED"),
        (f"{total_signals}", "SIGNALS COLLECTED"),
        (f"{len(source_counts)}", "SOURCES SCANNED"),
        (SECTOR_LABELS.get(top_sector, top_sector), "LEADING SECTOR"),
    ]
    cells = [
        [
            Paragraph(
                value,
                styles["stat_number"] if value.isdigit() else styles["stat_text"],
            ),
            Paragraph(_tracked(label, spaced), styles["stat_label"]),
        ]
        for value, label in tiles
    ]
    table = Table(
        [[cell for cell in cells]],
        colWidths=[content_width / len(cells)] * len(cells),
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEAFTER", (0, 0), (-2, 0), 0.5, RULE),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (-1, 0), 14),
                ("RIGHTPADDING", (0, 0), (-1, 0), 10),
                ("TOPPADDING", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
            ]
        )
    )
    return table


def _company_flowables(
    company: Company, styles: dict, fonts: dict[str, str], content_width: float
) -> list:
    name_row = Table(
        [
            [
                Paragraph(_escape(company.name), styles["company"]),
                _ScorePill(company.score, fonts["Scout-Display"]),
            ]
        ],
        colWidths=[content_width - 0.7 * inch, 0.7 * inch],
    )
    name_row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    meta_bits = [
        f'<font name="{fonts["Scout-Sans-Medium"]}" color="{ACCENT_HEX}">'
        f"{_escape(SECTOR_LABELS.get(company.sector, company.sector).upper())}</font>",
        _escape(company.country),
    ]
    if company.year_incorporated:
        meta_bits.append(f"inc. {company.year_incorporated}")
    if company.offering_usd:
        meta_bits.append(f"{_fmt_usd(company.offering_usd)} offering")
    meta = Paragraph(
        f'  <font color="{FAINT_HEX}">·</font>  '.join(meta_bits), styles["meta"]
    )

    card: list = [name_row, meta]

    if company.summary:
        card.append(Paragraph(_escape(company.summary), styles["body"]))

    if company.people:
        card.append(
            Paragraph(
                f'<font name="{fonts["Scout-Serif-Bold"]}">People</font>&nbsp;&nbsp;'
                f"{_escape(', '.join(company.people[:6]))}",
                styles["small"],
            )
        )

    if company.score_reasons:
        card.append(
            Paragraph(
                f'<font name="{fonts["Scout-Serif-Bold"]}">Why it surfaced</font>&nbsp;&nbsp;'
                f"{_escape('; '.join(company.score_reasons))}",
                styles["small"],
            )
        )

    links = ListFlowable(
        [
            ListItem(
                Paragraph(
                    f'<link href="{_escape(s.url)}" color="{ACCENT_HEX}">'
                    f"{_escape(s.title[:110])}</link> "
                    f'<font color="{MUTED_HEX}">({_escape(s.source)}'
                    f'{" — " + _escape(s.detail) if s.detail else ""})</font>',
                    styles["link"],
                ),
                leftIndent=10,
                bulletColor=ACCENT,
            )
            for s in company.signals[:6]
        ],
        bulletType="bullet",
        start="•",
        bulletFontSize=6,
        leftIndent=12,
    )
    card.append(Spacer(1, 3))
    card.append(links)

    return [
        KeepTogether(card),
        Spacer(1, 9),
        HRFlowable(width="100%", thickness=0.5, color=RULE),
        Spacer(1, 9),
    ]


def build(companies: list[Company], out_dir: str, source_counts: dict[str, int]) -> str:
    os.makedirs(out_dir, exist_ok=True)
    today = date.today()
    path = os.path.join(out_dir, f"biotech-scout-{today.isoformat()}.pdf")

    fonts, spaced = _register_fonts()
    styles = _styles(fonts, spaced)
    doc = SimpleDocTemplate(
        path,
        pagesize=LETTER,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.9 * inch,
        title=f"Biotech Scout — {today.isoformat()}",
        author="biotech-scout",
    )
    content_width = LETTER[0] - doc.leftMargin - doc.rightMargin
    paint = _make_page_painter(fonts, spaced, today)

    by_sector: dict[str, list[Company]] = {}
    for company in companies:
        by_sector.setdefault(company.sector, []).append(company)

    flow: list = []
    flow.append(
        Paragraph(_tracked("BIOTECH SCOUT · WEEKLY", spaced), styles["overline"])
    )
    flow.append(Paragraph("Early-Stage Signal Report", styles["title"]))
    flow.append(
        Paragraph(
            f"Week ending {today.strftime('%B %d, %Y')} — every company below "
            f"cleared the signal threshold in the last seven days.",
            styles["subtitle"],
        )
    )

    if not companies:
        flow.append(HRFlowable(width="100%", thickness=0.5, color=RULE, spaceAfter=16))
        flow.append(
            Paragraph(
                "No companies cleared the threshold this week. This is a normal "
                "outcome for a high-precision filter — quiet weeks happen, "
                "especially around holidays when EDGAR and NIH publish less.",
                styles["footnote"],
            )
        )
        doc.build(flow, onFirstPage=paint, onLaterPages=paint)
        return path

    flow.append(HRFlowable(width="100%", thickness=0.5, color=RULE))
    flow.append(
        _stat_band(companies, source_counts, by_sector, styles, spaced, content_width)
    )
    flow.append(HRFlowable(width="100%", thickness=0.5, color=RULE))
    flow.append(
        Paragraph(
            "SCANNED  ·  "
            + "   ".join(f"{k} ({v})" for k, v in sorted(source_counts.items())),
            styles["sources_line"],
        )
    )
    flow.append(Spacer(1, 6))

    for sector in sorted(by_sector, key=lambda s: -len(by_sector[s])):
        group = by_sector[sector]
        flow.append(
            Paragraph(
                f"{SECTOR_LABELS.get(sector, sector)}"
                f'&nbsp;&nbsp;<font color="{ACCENT_HEX}">{len(group)}</font>',
                styles["section"],
            )
        )
        flow.append(_SectionMark())
        flow.append(Spacer(1, 10))
        for company in group:
            flow.extend(_company_flowables(company, styles, fonts, content_width))

    flow.append(PageBreak())
    flow.append(Paragraph("How to read this", styles["section"]))
    flow.append(_SectionMark())
    flow.append(Spacer(1, 10))
    flow.append(
        Paragraph(
            "Companies are surfaced by a signal score, not a quality judgment. A high "
            "score means several independent public sources agree that a real, young, "
            "biotech-shaped company did something this week — it says nothing about "
            "whether the science or the team is any good. Sector labels and one-line "
            "summaries are generated from the source text and can be wrong when that "
            "text is thin. Every claim links back to its primary source; check the "
            "source before acting on anything here.",
            styles["body"],
        )
    )
    flow.append(
        Paragraph(
            "Coverage is uneven by design: US private raises (SEC Form D) and US "
            "non-dilutive grants (NIH RePORTER) are well covered, the UK and EU "
            "partially, and everywhere else only when a preprint or a news story "
            "happens to surface. Absence from this report is not evidence that a "
            "company does not exist.",
            styles["body"],
        )
    )

    doc.build(flow, onFirstPage=paint, onLaterPages=paint)
    return path
