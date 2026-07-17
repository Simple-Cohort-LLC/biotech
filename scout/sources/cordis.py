"""EU CORDIS — Horizon Europe / EIC grant recipients.

EIC Accelerator and Horizon health-cluster grants are the closest EU analogue to
an SBIR award: reviewed, non-dilutive, and often the first public trace of a
deep-tech spinout.

CAVEAT: CORDIS is the least stable source wired up here. Its public search
endpoint is not versioned or formally documented, and the response shape has
changed before. This module fails soft by design — if the endpoint moves, the
run logs a warning and continues without EU coverage rather than dying. If EU
deal flow matters to you and this goes quiet for a few weeks, the fallback is
the CORDIS bulk CSV export at https://cordis.europa.eu/data/.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import requests

from ..http import get
from ..models import Company, Signal

log = logging.getLogger(__name__)

SEARCH_URL = "https://cordis.europa.eu/api/search/results"

QUERY_TERMS = (
    "biotechnology OR therapeutics OR diagnostics OR 'synthetic biology' "
    "OR biomanufacturing OR 'drug discovery' OR genomics"
)

ACADEMIC_MARKERS = (
    "university", "universite", "universität", "universidad", "universita",
    "institut", "hospital", "college", "academy", "cnrs", "inserm",
    "max planck", "fraunhofer", "school", "foundation", "consiglio",
)


def _looks_like_company(name: str) -> bool:
    lowered = name.lower()
    return not any(marker in lowered for marker in ACADEMIC_MARKERS)


def _extract_projects(payload: object) -> list[dict]:
    """Dig the project list out of whatever shape CORDIS returned."""
    if not isinstance(payload, dict):
        return []
    hits = payload.get("hits")
    if isinstance(hits, dict):
        items = hits.get("hit") or hits.get("hits") or []
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
    for key in ("results", "payload", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [i for i in value if isinstance(i, dict)]
    return []


def _field(item: dict, *names: str) -> str:
    source = item.get("project") if isinstance(item.get("project"), dict) else item
    for name in names:
        value = source.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value and isinstance(value[0], str):
            return value[0].strip()
    return ""


def fetch(session: requests.Session, lookback_days: int) -> list[Company]:
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    params = {
        "q": f"contenttype='project' AND ({QUERY_TERMS}) AND startDate>='{since}'",
        "format": "json",
        "p": 1,
        "num": 100,
    }

    try:
        resp = get(session, SEARCH_URL, params=params)
    except requests.RequestException as exc:
        log.warning("CORDIS request failed (EU coverage skipped this run): %s", exc)
        return []
    if not resp.ok:
        log.warning(
            "CORDIS returned %s (EU coverage skipped this run). "
            "If this persists, the search endpoint likely changed.",
            resp.status_code,
        )
        return []

    try:
        payload = resp.json()
    except ValueError:
        log.warning("CORDIS returned non-JSON (EU coverage skipped this run)")
        return []

    projects = _extract_projects(payload)
    if not projects:
        log.warning(
            "CORDIS returned no parseable projects — response shape may have changed"
        )
        return []

    companies: list[Company] = []
    for item in projects:
        title = _field(item, "title")
        acronym = _field(item, "acronym")
        objective = _field(item, "objective", "teaser", "description")
        rcn = _field(item, "rcn", "id")
        name = acronym or title
        if not name or not _looks_like_company(name):
            continue

        company = Company(
            name=name,
            country="EU",
            description=objective[:2000],
        )
        company.signals.append(
            Signal(
                source="cordis",
                kind="eu_grant",
                title=f"EU grant: {title[:200]}" if title else "EU grant",
                url=(
                    f"https://cordis.europa.eu/project/id/{rcn}"
                    if rcn
                    else "https://cordis.europa.eu"
                ),
                observed_on=date.today(),
                detail="Horizon Europe / EIC funded project",
            )
        )
        companies.append(company)

    log.info("CORDIS: %d company-like EU grant recipients", len(companies))
    return companies
