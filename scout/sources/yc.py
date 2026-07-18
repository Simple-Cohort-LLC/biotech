"""Y Combinator biotech directory — recent batches approaching Demo Day.

YC publishes structured company records inside its public biotech directory.
Recent-batch companies are a predictive fundraising signal: they often begin
investor conversations before a financing announcement or Form D appears.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from html.parser import HTMLParser

import requests

from ..http import get
from ..models import Company, Signal

log = logging.getLogger(__name__)

DIRECTORY_URL = "https://www.ycombinator.com/companies/industry/biotech"
COMPANY_URL = "https://www.ycombinator.com{path}"
_BATCH_RE = re.compile(r"^[swfp](\d{4})$", re.IGNORECASE)


class _PageDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page_data: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.page_data is not None:
            return
        values = dict(attrs)
        raw = values.get("data-page")
        if raw and '"companies"' in raw:
            self.page_data = raw


def _parse_page(html: str) -> tuple[list[dict], int]:
    parser = _PageDataParser()
    parser.feed(html)
    if not parser.page_data:
        raise ValueError("YC directory did not contain structured company data")
    payload = json.loads(parser.page_data)
    props = payload.get("props", {})
    return props.get("companies", []), int(props.get("totalPages", 1))


def _to_company(record: dict, min_batch_year: int) -> Company | None:
    batch = str(record.get("batch_name") or "")
    match = _BATCH_RE.match(batch)
    if not match or int(match.group(1)) < min_batch_year:
        return None
    if str(record.get("ycdc_status") or "").lower() != "active":
        return None

    name = str(record.get("name") or "").strip()
    path = str(record.get("ycdc_company_url") or "").strip()
    if not name or not path.startswith("/companies/"):
        return None

    one_liner = str(record.get("one_liner") or "").strip()
    long_description = str(record.get("long_description") or "").strip()
    description = "\n\n".join(part for part in (one_liner, long_description) if part)
    company = Company(
        name=name,
        country=str(record.get("country") or "unknown"),
        description=description,
        website=str(record.get("website") or "").strip() or None,
        year_incorporated=record.get("year_founded") if isinstance(record.get("year_founded"), int) else None,
    )
    company.signals.append(
        Signal(
            source="yc",
            kind="yc_recent_batch",
            title=f"Y Combinator {batch.upper()} biotech batch",
            url=COMPANY_URL.format(path=path),
            observed_on=date.today(),
            detail=batch.upper(),
        )
    )
    return company


def fetch(session: requests.Session, max_batch_age_years: int = 1) -> list[Company]:
    min_batch_year = date.today().year - max(0, max_batch_age_years)
    companies: list[Company] = []
    page = 1
    total_pages = 1

    while page <= total_pages:
        resp = get(session, DIRECTORY_URL, params={"page": page} if page > 1 else None)
        if not resp.ok:
            log.warning("YC directory page %d returned %s", page, resp.status_code)
            break
        records, total_pages = _parse_page(resp.text)
        found_recent = False
        for record in records:
            company = _to_company(record, min_batch_year)
            if company:
                companies.append(company)
                found_recent = True

        # The directory is newest-first. Once a full page has no eligible
        # batches, later pages cannot contain any either.
        if not found_recent:
            break
        page += 1

    log.info("Y Combinator: %d active biotech companies from batches since %d", len(companies), min_batch_year)
    return companies
