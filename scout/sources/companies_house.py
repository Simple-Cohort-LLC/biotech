"""UK Companies House — new biotech incorporations.

The main non-US structured source. Free API key from
https://developer.company-information.service.gov.uk/. Auth is HTTP Basic with
the key as the username and an empty password.

A fresh incorporation is a weak signal on its own — it means someone registered
a company, not that they raised or built anything. It earns its place by
corroborating the other sources, which is why score.py weights it lightly.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import requests

from ..http import get
from ..models import Company, Signal

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.company-information.service.gov.uk/advanced-search/companies"
PROFILE_URL = "https://find-and-update.company-information.service.gov.uk/company/{number}"

# UK SIC codes that map onto biotech.
SIC_CODES = {
    "72110": "Research and experimental development on biotechnology",
    "21100": "Manufacture of basic pharmaceutical products",
    "21200": "Manufacture of pharmaceutical preparations",
    "26600": "Manufacture of irradiation, electromedical equipment",
    "32500": "Manufacture of medical and dental instruments",
    "72190": "Other research on natural sciences and engineering",
}


def fetch(session: requests.Session, lookback_days: int, api_key: str | None) -> list[Company]:
    if not api_key:
        log.info("Companies House: no API key set, skipping")
        return []

    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    until = date.today().isoformat()
    companies: list[Company] = []

    for sic, label in SIC_CODES.items():
        params = {
            "sic_codes": sic,
            "incorporated_from": since,
            "incorporated_to": until,
            "company_status": "active",
            "size": 100,
        }
        try:
            resp = get(session, SEARCH_URL, params=params, auth=(api_key, ""))
        except requests.RequestException as exc:
            log.warning("Companies House request failed for SIC %s: %s", sic, exc)
            continue
        if resp.status_code == 401:
            log.error("Companies House rejected the API key (401); skipping source")
            return companies
        if not resp.ok:
            log.warning("Companies House SIC %s returned %s", sic, resp.status_code)
            continue

        for item in resp.json().get("items", []):
            name = item.get("company_name")
            number = item.get("company_number")
            if not name or not number:
                continue
            incorporated = item.get("date_of_creation") or ""
            company = Company(
                name=name.title() if name.isupper() else name,
                country="UK",
                description=f"UK company, SIC {sic}: {label}.",
                year_incorporated=int(incorporated[:4]) if incorporated[:4].isdigit() else None,
            )
            company.signals.append(
                Signal(
                    source="companies_house",
                    kind="incorporation",
                    title=f"Incorporated in the UK — {label}",
                    url=PROFILE_URL.format(number=number),
                    observed_on=date.today(),
                    detail=f"Incorporated {incorporated}" if incorporated else "",
                )
            )
            companies.append(company)

    log.info("Companies House: %d new biotech incorporations", len(companies))
    return companies
