"""SEC Form D — new private raises in the US.

Form D is filed within 15 days of the first sale in an exempt offering, so it is
the fastest legally-guaranteed signal that a US startup has raised money. Each
filing carries the issuer name, industry group, offering size, year of
incorporation, and named executives/directors.

Flow: daily form index -> Form D accessions -> primary_doc.xml per filing.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import requests

from ..http import get
from ..models import Company, Signal

log = logging.getLogger(__name__)

INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{qtr}/form.{ymd}.idx"
PRIMARY_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/primary_doc.xml"
FILING_PAGE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"

# Form D's own industry taxonomy. These are the groups worth looking at; note
# that "Pooled Investment Fund" is the single biggest noise source in Form D and
# is excluded outright below.
BIOTECH_INDUSTRY_GROUPS = {
    "Biotechnology",
    "Pharmaceuticals",
    "Other Health Care",
    "Health Care",
    "Hospitals & Physicians",
    "Medical Devices",
    "Agriculture",
}
EXCLUDED_INDUSTRY_GROUPS = {
    "Pooled Investment Fund",
    "Investing",
    "Commercial Banking",
    "Insurance",
    "Real Estate",
    "Oil & Gas",
    "REITS & Finance",
}

_ACCESSION_RE = re.compile(r"(\d{10}-\d{2}-\d{6})")


def _strip_ns(tree: ET.Element) -> ET.Element:
    for el in tree.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return tree


def _quarter(day: date) -> int:
    return (day.month - 1) // 3 + 1


def _parse_index(text: str) -> list[tuple[str, str, str]]:
    """Return (company_name, cik, accession_nodash) for each Form D row.

    form.idx is fixed-width-ish and space-padded. We split on runs of 2+ spaces
    rather than by column offset, which the SEC has shifted historically.
    """
    out: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        if not line.startswith("D "):
            continue
        parts = [p.strip() for p in re.split(r"\s{2,}", line.strip()) if p.strip()]
        if len(parts) < 5:
            continue
        form_type, company_name, cik, _date_filed, filename = parts[:5]
        if form_type not in {"D", "D/A"}:
            continue
        match = _ACCESSION_RE.search(filename)
        if not match:
            continue
        out.append((company_name, cik, match.group(1).replace("-", "")))
    return out


def _text(root: ET.Element, path: str) -> str:
    value = root.findtext(path)
    return value.strip() if value else ""


def _int_or_none(raw: str) -> int | None:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _parse_form_d(xml_text: str, cik: str, accession: str) -> Company | None:
    try:
        root = _strip_ns(ET.fromstring(xml_text))
    except ET.ParseError as exc:
        log.debug("Form D XML parse failed for %s: %s", accession, exc)
        return None

    name = _text(root, "primaryIssuer/entityName")
    if not name:
        return None

    industry = _text(root, "offeringData/industryGroup/industryGroupType")
    if industry in EXCLUDED_INDUSTRY_GROUPS:
        return None
    if industry not in BIOTECH_INDUSTRY_GROUPS:
        return None
    # Fund-of-funds sometimes file under a health-care group; the presence of
    # investmentFundInfo is the giveaway.
    if root.find("offeringData/industryGroup/investmentFundInfo") is not None:
        return None

    year = _int_or_none(_text(root, "primaryIssuer/yearOfInc/value"))
    over_five = _text(root, "primaryIssuer/yearOfInc/overFiveYears").lower() == "true"
    if over_five and year is None:
        year = 0  # sentinel: "incorporated more than five years ago"

    offering = _int_or_none(_text(root, "offeringData/offeringSalesAmounts/totalOfferingAmount"))
    sold = _int_or_none(_text(root, "offeringData/offeringSalesAmounts/totalAmountSold"))

    people: list[str] = []
    for person in root.findall("relatedPersonsList/relatedPersonInfo"):
        first = _text(person, "relatedPersonName/firstName")
        last = _text(person, "relatedPersonName/lastName")
        full = " ".join(p for p in (first, last) if p)
        if full and full not in people:
            people.append(full)

    country = _text(root, "primaryIssuer/issuerAddress/stateOrCountry") or "US"
    # Form D's stateOrCountry is a US state code for domestic issuers.
    country = "US" if len(country) == 2 and country.isalpha() and country not in {"XX"} else country

    url = FILING_PAGE_URL.format(cik=cik.lstrip("0") or cik, accession=accession)
    company = Company(
        name=name,
        country="US",
        year_incorporated=year if year else None,
        offering_usd=offering,
        amount_sold_usd=sold,
        people=people[:8],
        description=f"Form D issuer, industry group: {industry}.",
    )
    company.signals.append(
        Signal(
            source="sec_form_d",
            kind="form_d_filing",
            title=f"Form D filed — {industry}",
            url=url,
            observed_on=date.today(),
            detail=(
                f"Offering ${offering:,}" if offering else "Offering amount indefinite"
            ),
        )
    )
    return company


def fetch(session: requests.Session, lookback_days: int) -> list[Company]:
    companies: list[Company] = []
    today = date.today()

    for offset in range(lookback_days):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5:  # EDGAR publishes no index on weekends
            continue
        url = INDEX_URL.format(
            year=day.year, qtr=_quarter(day), ymd=day.strftime("%Y%m%d")
        )
        try:
            resp = get(session, url)
        except requests.RequestException as exc:
            log.warning("EDGAR index fetch failed for %s: %s", day, exc)
            continue
        if resp.status_code == 404:
            # Holiday, or the index has not been published yet.
            continue
        if not resp.ok:
            log.warning("EDGAR index %s returned %s", day, resp.status_code)
            continue

        rows = _parse_index(resp.text)
        log.info("EDGAR %s: %d Form D filings", day, len(rows))

        for _name, cik, accession in rows:
            doc_url = PRIMARY_DOC_URL.format(cik=cik.lstrip("0") or cik, accession=accession)
            try:
                doc = get(session, doc_url)
            except requests.RequestException as exc:
                log.debug("Form D doc fetch failed %s: %s", accession, exc)
                continue
            if not doc.ok:
                continue
            company = _parse_form_d(doc.text, cik, accession)
            if company:
                companies.append(company)

    log.info("SEC Form D: %d biotech-relevant issuers", len(companies))
    return companies
