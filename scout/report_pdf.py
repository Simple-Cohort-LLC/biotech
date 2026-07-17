"""The weekly PDF: one card per company, links live, sorted by score."""

from __future__ import annotations

import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from .models import Company

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#6b6b6b")
RULE = colors.HexColor("#d8d8d8")
ACCENT = colors.HexColor("#0b6b5b")

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


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=22, leading=26, textColor=INK, alignment=TA_LEFT, spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, leading=14, textColor=MUTED, spaceAfter=16,
        ),
        "section": ParagraphStyle(
            "section", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=13, leading=16, textColor=ACCENT, spaceBefore=14, spaceAfter=6,
        ),
        "company": ParagraphStyle(
            "company", parent=base["Heading3"], fontName="Helvetica-Bold",
            fontSize=12, leading=15, textColor=INK, spaceBefore=10, spaceAfter=2,
        ),
        "meta": ParagraphStyle(
            "meta", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.5, leading=12, textColor=MUTED, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, leading=14, textColor=INK, spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "small", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.5, leading=12, textColor=MUTED,
        ),
        "link": ParagraphStyle(
            "link", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.5, leading=12, textColor=ACCENT,
        ),
    }


def _company_flowables(company: Company, styles: dict) -> list:
    flow: list = []
    flow.append(Paragraph(_escape(company.name), styles["company"]))

    meta_bits = [
        SECTOR_LABELS.get(company.sector, company.sector),
        company.country,
        f"signal score {company.score}",
    ]
    if company.year_incorporated:
        meta_bits.append(f"inc. {company.year_incorporated}")
    if company.offering_usd:
        meta_bits.append(f"offering ${company.offering_usd:,}")
    flow.append(Paragraph(" · ".join(_escape(b) for b in meta_bits), styles["meta"]))

    if company.summary:
        flow.append(Paragraph(_escape(company.summary), styles["body"]))

    if company.people:
        flow.append(
            Paragraph(
                f"<b>People:</b> {_escape(', '.join(company.people[:6]))}", styles["small"]
            )
        )

    if company.score_reasons:
        flow.append(
            Paragraph(
                f"<b>Why it surfaced:</b> {_escape('; '.join(company.score_reasons))}",
                styles["small"],
            )
        )

    links = ListFlowable(
        [
            ListItem(
                Paragraph(
                    f'<link href="{_escape(s.url)}" color="#0b6b5b">'
                    f"{_escape(s.title[:110])}</link> "
                    f'<font color="#6b6b6b">({_escape(s.source)}'
                    f'{" — " + _escape(s.detail) if s.detail else ""})</font>',
                    styles["link"],
                ),
                leftIndent=10,
            )
            for s in company.signals[:6]
        ],
        bulletType="bullet",
        start="•",
        bulletFontSize=7,
        leftIndent=12,
    )
    flow.append(Spacer(1, 3))
    flow.append(links)
    flow.append(Spacer(1, 6))
    flow.append(HRFlowable(width="100%", thickness=0.5, color=RULE, spaceAfter=2))
    return flow


def build(companies: list[Company], out_dir: str, source_counts: dict[str, int]) -> str:
    os.makedirs(out_dir, exist_ok=True)
    today = date.today()
    path = os.path.join(out_dir, f"biotech-scout-{today.isoformat()}.pdf")

    styles = _styles()
    doc = SimpleDocTemplate(
        path,
        pagesize=LETTER,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=f"Biotech Scout — {today.isoformat()}",
        author="biotech-scout",
    )

    flow: list = []
    flow.append(Paragraph("Early-Stage Biotech Scout", styles["title"]))
    flow.append(
        Paragraph(
            f"Week ending {today.strftime('%B %d, %Y')} · {len(companies)} companies "
            f"cleared the signal threshold · sources scanned: "
            f"{', '.join(f'{k} ({v})' for k, v in sorted(source_counts.items()))}",
            styles["subtitle"],
        )
    )
    flow.append(HRFlowable(width="100%", thickness=1, color=INK, spaceAfter=10))

    if not companies:
        flow.append(
            Paragraph(
                "No companies cleared the threshold this week. This is a normal "
                "outcome for a high-precision filter — quiet weeks happen, "
                "especially around holidays when EDGAR and NIH publish less.",
                styles["body"],
            )
        )
        doc.build(flow)
        return path

    by_sector: dict[str, list[Company]] = {}
    for company in companies:
        by_sector.setdefault(company.sector, []).append(company)

    for sector in sorted(by_sector, key=lambda s: -len(by_sector[s])):
        group = by_sector[sector]
        flow.append(
            Paragraph(
                f"{SECTOR_LABELS.get(sector, sector)} ({len(group)})", styles["section"]
            )
        )
        for company in group:
            flow.extend(_company_flowables(company, styles))

    flow.append(PageBreak())
    flow.append(Paragraph("How to read this", styles["section"]))
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

    doc.build(flow)
    return path
