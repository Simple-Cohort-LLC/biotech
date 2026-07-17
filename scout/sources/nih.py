"""NIH RePORTER — SBIR/STTR awards.

An SBIR Phase I award is strong non-dilutive validation for a pre-seed biotech:
it means an NIH study section reviewed the science and funded it. Phase I
(R41/R43) is the earliest-stage signal; Phase II (R42/R44) means Phase I
already cleared its milestones.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import requests

from ..http import post
from ..models import Company, Signal

log = logging.getLogger(__name__)

API_URL = "https://api.reporter.nih.gov/v2/projects/search"
PROJECT_URL = "https://reporter.nih.gov/project-details/{appl_id}"

ACTIVITY_CODES = {
    "R41": "STTR Phase I",
    "R43": "SBIR Phase I",
    "R42": "STTR Phase II",
    "R44": "SBIR Phase II",
}

# Awards to academic institutions are not companies.
ACADEMIC_MARKERS = (
    "university",
    "college",
    "school of",
    "institute of technology",
    "hospital",
    "medical center",
    "health system",
    "foundation",
    "trustees",
    "regents",
)


def _looks_like_company(org_name: str) -> bool:
    lowered = org_name.lower()
    return not any(marker in lowered for marker in ACADEMIC_MARKERS)


def fetch(session: requests.Session, lookback_days: int) -> list[Company]:
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    until = date.today().isoformat()

    payload = {
        "criteria": {
            "activity_codes": list(ACTIVITY_CODES),
            "award_notice_date": {"from_date": since, "to_date": until},
        },
        "include_fields": [
            "ApplId",
            "ProjectTitle",
            "AbstractText",
            "Organization",
            "PrincipalInvestigators",
            "AwardAmount",
            "ActivityCode",
            "AwardNoticeDate",
        ],
        "offset": 0,
        "limit": 500,
    }

    companies: list[Company] = []
    try:
        resp = post(session, API_URL, json=payload, headers={"Content-Type": "application/json"})
    except requests.RequestException as exc:
        log.warning("NIH RePORTER request failed: %s", exc)
        return []
    if not resp.ok:
        log.warning("NIH RePORTER returned %s: %s", resp.status_code, resp.text[:300])
        return []

    for item in resp.json().get("results", []):
        org = (item.get("organization") or {}).get("org_name") or ""
        if not org or not _looks_like_company(org):
            continue

        activity = item.get("activity_code", "")
        phase = ACTIVITY_CODES.get(activity, activity)
        abstract = (item.get("abstract_text") or "").strip()
        title = item.get("project_title") or ""
        appl_id = item.get("appl_id")
        amount = item.get("award_amount")

        pis = [
            pi.get("full_name")
            for pi in (item.get("principal_investigators") or [])
            if pi.get("full_name")
        ]

        country = (item.get("organization") or {}).get("org_country") or "US"

        company = Company(
            name=org.title() if org.isupper() else org,
            country="US" if country in ("UNITED STATES", "US", "") else country.title(),
            description=abstract[:2000],
            people=pis[:8],
        )
        company.signals.append(
            Signal(
                source="nih_reporter",
                kind=f"sbir_{'phase_1' if activity in ('R41', 'R43') else 'phase_2'}",
                title=f"{phase}: {title}",
                url=PROJECT_URL.format(appl_id=appl_id) if appl_id else "https://reporter.nih.gov",
                observed_on=date.today(),
                detail=f"Award ${amount:,}" if isinstance(amount, (int, float)) else phase,
            )
        )
        companies.append(company)

    log.info("NIH RePORTER: %d company awards", len(companies))
    return companies
