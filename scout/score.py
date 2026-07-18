"""Signal scoring — the rubric that decides what reaches the digest.

The whole point of the "high precision" setting is here. Two ways to clear the
bar (default 55):

  1. Multi-source corroboration. A company that shows up in two independent
     sources in the same week is almost never noise.
  2. One very strong single signal. A first-time Form D from a recently
     incorporated biotech issuer raising a seed-sized round is worth surfacing
     on its own — that is the canonical pre-A event.

Everything else — a lone incorporation, a lone preprint, a lone headline — is
recorded in the database but does not reach Slack. Adjust MIN_SCORE in the
environment to loosen or tighten this without touching code.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from .config import Config
from .models import Company

log = logging.getLogger(__name__)

# Per-source base value: how much one signal from this source is worth alone.
SOURCE_WEIGHTS = {
    "sec_form_d": 35,      # a real, legally-required financing event
    "nih_reporter": 30,    # peer-reviewed non-dilutive validation
    "news": 20,            # real but unverified; noisy
    "biorxiv": 15,         # proves technical activity, not a company
    "medrxiv": 15,
    "cordis": 20,          # EU equivalent of an SBIR, but coarser data
    "companies_house": 8,  # someone registered a company; weak alone
    "yc": 35,              # recent cohort: strong pre-raise prior, not a financing event
}

CORROBORATION_BONUS = 30  # per additional independent source


def score(company: Company, config: Config) -> Company:
    points = 0
    reasons: list[str] = []

    sources = company.sources
    if not sources:
        company.score = 0
        return company

    # 1. Strongest single source sets the floor.
    best_source = max(sources, key=lambda s: SOURCE_WEIGHTS.get(s, 5))
    points += SOURCE_WEIGHTS.get(best_source, 5)
    reasons.append(f"Primary source: {best_source}")

    # 2. Independent corroboration is the highest-value signal available.
    extra_sources = len(sources) - 1
    if extra_sources > 0:
        points += CORROBORATION_BONUS * min(extra_sources, 2)
        reasons.append(
            f"Corroborated by {extra_sources} additional source"
            f"{'s' if extra_sources > 1 else ''}: {', '.join(sorted(sources - {best_source}))}"
        )

    # 3. Form D specifics — the pre-A shape.
    if "sec_form_d" in sources:
        if company.offering_usd is not None:
            if company.offering_usd <= 5_000_000:
                points += 15
                reasons.append(f"Seed-sized raise (${company.offering_usd:,})")
            elif company.offering_usd <= config.max_offering_usd:
                points += 8
                reasons.append(f"Sub-Series-A raise (${company.offering_usd:,})")
            else:
                points -= 25
                reasons.append(
                    f"Raise above the pre-A ceiling (${company.offering_usd:,}) — likely past seed"
                )
        if company.is_first_form_d:
            points += 12
            reasons.append("First Form D from this issuer")

    # 4. SBIR/STTR Phase I is the earliest-stage grant signal.
    kinds = {s.kind for s in company.signals}
    if "yc_recent_batch" in kinds:
        batch_years = [
            int(match.group(1))
            for signal in company.signals
            if signal.kind == "yc_recent_batch"
            for match in [re.search(r"(\d{4})", signal.detail)]
            if match
        ]
        if date.today().year in batch_years:
            points += 25
            reasons.append("Current-year YC batch (likely approaching or in fundraising)")
        else:
            points += 10
            reasons.append("Recent YC batch (predictive fundraising signal)")

    if "raising_now" in kinds:
        points += 35
        reasons.append("Explicit public signal that the round is currently open")
    elif "planning_to_raise" in kinds:
        points += 35
        reasons.append("Explicit public signal that a raise is being prepared")

    if "sbir_phase_1" in kinds:
        points += 12
        reasons.append("SBIR/STTR Phase I award (earliest-stage grant)")
    elif "sbir_phase_2" in kinds:
        points += 6
        reasons.append("SBIR/STTR Phase II award (Phase I milestones cleared)")

    # 5. Company age.
    if company.year_incorporated:
        age = date.today().year - company.year_incorporated
        if age <= 3:
            points += 12
            reasons.append(f"Incorporated {company.year_incorporated} ({age}y old)")
        elif age > config.max_company_age_years:
            points -= 20
            reasons.append(f"Founded {company.year_incorporated} — likely past early stage")

    # 6. Named people give you something to actually diligence.
    if len(company.people) >= 2:
        points += 5
        reasons.append(f"{len(company.people)} named principals")

    # 7. A confident sector call means the description was substantive.
    if company.sector != "unknown" and company.sector_confidence >= 0.7:
        points += 5
        reasons.append(f"Clear sector signal: {company.sector}")
    elif company.sector == "unknown":
        points -= 10
        reasons.append("Sector could not be determined from available text")

    company.score = max(0, min(points, 100))
    company.score_reasons = reasons
    return company


def rank(companies: list[Company], config: Config) -> list[Company]:
    for company in companies:
        score(company, config)
    return sorted(companies, key=lambda c: c.score, reverse=True)
