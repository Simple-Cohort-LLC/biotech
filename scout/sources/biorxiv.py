"""bioRxiv / medRxiv — company-affiliated preprints.

Catches academic spinouts before they file or raise anything, and it is
genuinely global. Most preprints are academic, so the whole value here is the
affiliation filter: we keep only papers whose corresponding institution looks
like a company rather than a university or hospital.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

import requests

from ..http import get
from ..models import Company, Signal

log = logging.getLogger(__name__)

API_URL = "https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}"
PAGE_SIZE = 100

COMPANY_MARKERS = (
    " inc", " inc.", " llc", " ltd", " limited", " corp", " corporation",
    " gmbh", " ag", " sa", " bv", " nv", " ab", " oy", " aps", " pte",
    "therapeutics", "biosciences", "bioscience", "biotech", "pharmaceutical",
    "pharma", "laboratories", "labs", " bio", "genomics", "diagnostics",
)
ACADEMIC_MARKERS = (
    "university", "universite", "universität", "universidad", "universita",
    "college", "school of", "institute", "institut", "istituto", "hospital",
    "medical center", "medical centre", "health system", "academy", "faculty",
    "department of", "nhs", "inserm", "cnrs", "max planck", "clinic",
    "council", "consejo", "agency", "agencia", "national instit", "ministry",
    "foundation", "fondation", "centre national", "center for", "centre for",
    "polytechni", "klinik", "hospices", "gov", "état", "estatal",
)

# Numeric affiliation footnote markers ("1Company, 2University") get stripped
# before matching so the leading digit doesn't defeat the marker checks.
_LEADING_NUM = re.compile(r"^\s*\d+\s*")


def _is_company_affiliation(institution: str) -> bool:
    cleaned = _LEADING_NUM.sub("", institution)
    lowered = f" {cleaned.lower().strip()} "
    if any(marker in lowered for marker in ACADEMIC_MARKERS):
        return False
    return any(marker in lowered for marker in COMPANY_MARKERS)


def _fetch_server(session: requests.Session, server: str, lookback_days: int) -> list[Company]:
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    end = date.today().isoformat()
    companies: list[Company] = []
    cursor = 0

    while True:
        url = API_URL.format(server=server, start=start, end=end, cursor=cursor)
        try:
            resp = get(session, url)
        except requests.RequestException as exc:
            log.warning("%s fetch failed at cursor %s: %s", server, cursor, exc)
            break
        if not resp.ok:
            log.warning("%s returned %s", server, resp.status_code)
            break

        body = resp.json()
        items = body.get("collection", [])
        if not items:
            break

        for item in items:
            institution = (item.get("author_corresponding_institution") or "").strip()
            if not institution or not _is_company_affiliation(institution):
                continue
            doi = item.get("doi", "")
            company = Company(
                name=institution,
                description=(item.get("abstract") or "").strip()[:2000],
                people=[item.get("author_corresponding", "")] if item.get("author_corresponding") else [],
            )
            company.signals.append(
                Signal(
                    source=server,
                    kind="preprint",
                    title=item.get("title", "")[:300],
                    url=f"https://doi.org/{doi}" if doi else f"https://www.{server}.org",
                    observed_on=date.today(),
                    detail=f"Category: {item.get('category', 'n/a')}",
                )
            )
            companies.append(company)

        total = int(body.get("messages", [{}])[0].get("total", 0) or 0)
        cursor += PAGE_SIZE
        if cursor >= total or cursor > 3000:  # safety bound on very busy weeks
            break

    return companies


def fetch(session: requests.Session, lookback_days: int) -> list[Company]:
    companies: list[Company] = []
    for server in ("biorxiv", "medrxiv"):
        found = _fetch_server(session, server, lookback_days)
        log.info("%s: %d company-affiliated preprints", server, len(found))
        companies.extend(found)
    return companies
