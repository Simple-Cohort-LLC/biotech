"""Claude-backed classification and news extraction, with a rules fallback.

Two jobs:
  1. classify() — assign a sector, write the digest one-liner, and drop records
     that are not actually early-stage biotech companies.
  2. extract_from_headlines() — pull company names out of news headlines, which
     no regex does well.

If ANTHROPIC_API_KEY is missing or the API errors, classification degrades to
the keyword rulebook in taxonomy.py and headline extraction is skipped. The run
still produces a digest; it just gets coarser sectors and no news coverage.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from .models import SECTORS, Company, Signal
from .taxonomy import classify as rules_classify
from .taxonomy import looks_like_biotech

log = logging.getLogger(__name__)

BATCH_SIZE = 20

CLASSIFY_SYSTEM = """You triage early-stage biotech companies for an angel investor.

For each record you are given a company name and whatever text we scraped about \
it (a grant abstract, a Form D industry group, a preprint abstract, a company \
registry description). The text is often thin or generic — that is expected.

For each record, return:
- is_biotech: false if this is not a life-sciences company at all (a fund, an \
SPV, a staffing agency, a real-estate entity, a pure software company with no \
life-sciences application, a university lab, a hospital).
- is_early_stage: false if this is clearly a large, established, or public \
company, a subsidiary of a large pharma, or a contract research organization.
- sector: one of the allowed values. Use "unknown" only when the text genuinely \
does not support any choice — do not guess from the company name alone.
- confidence: 0.0-1.0 for the sector call.
- summary: one sentence, max 25 words, describing what the company actually \
does. Write it for a reader deciding whether to spend ten minutes on this \
company. If the text does not say what they do, say so plainly rather than \
inventing a plausible description.

Never invent facts that are not in the provided text."""

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "is_biotech": {"type": "boolean"},
                    "is_early_stage": {"type": "boolean"},
                    "sector": {"type": "string", "enum": SECTORS},
                    "confidence": {"type": "number"},
                    "summary": {"type": "string"},
                },
                "required": [
                    "index", "is_biotech", "is_early_stage",
                    "sector", "confidence", "summary",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["records"],
    "additionalProperties": False,
}

EXTRACT_SYSTEM = """You extract startup names from funding news headlines.

Return one record per headline that announces a specific, named, early-stage \
life-sciences company raising money or launching. Skip the headline entirely if:
- no specific company is named (industry roundups, market reports, "5 startups to watch")
- the company is large, public, or well-established
- the round is Series A or later
- it is not a life-sciences company

Do not guess a company name from a vague headline. Returning fewer, correct \
records is much better than returning speculative ones."""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "headline_index": {"type": "integer"},
                    "company_name": {"type": "string"},
                    "country": {"type": "string"},
                    "sector": {"type": "string", "enum": SECTORS},
                    "round_stage": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": [
                    "headline_index", "company_name", "country",
                    "sector", "round_stage", "summary",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["companies"],
    "additionalProperties": False,
}


def _client(api_key: str):
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def _ask(client, model: str, system: str, prompt: str, schema: dict) -> dict | None:
    try:
        response = client.messages.create(
            model=model,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": schema},
            },
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001 - any API failure degrades to rules
        log.warning("Claude call failed: %s", exc)
        return None

    if response.stop_reason == "refusal":
        log.warning("Claude declined the classification request")
        return None

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("Claude returned unparseable JSON")
        return None


def _rules_only(companies: list[Company]) -> list[Company]:
    kept: list[Company] = []
    for company in companies:
        blob = f"{company.name} {company.description}"
        if not looks_like_biotech(blob):
            continue
        sector, confidence = rules_classify(company.name, company.description)
        company.sector = sector
        company.sector_confidence = confidence
        company.summary = (
            company.description[:160].strip().replace("\n", " ")
            or f"{company.name} — no description available from source data."
        )
        kept.append(company)
    return kept


def classify(companies: list[Company], api_key: str | None, model: str) -> list[Company]:
    """Classify and filter. Returns only records judged early-stage biotech."""
    if not companies:
        return []
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — falling back to the keyword rulebook")
        return _rules_only(companies)

    client = _client(api_key)
    kept: list[Company] = []

    for start in range(0, len(companies), BATCH_SIZE):
        batch = companies[start : start + BATCH_SIZE]
        payload = [
            {
                "index": i,
                "name": c.name,
                "country": c.country,
                "signals": [f"{s.source}: {s.title}" for s in c.signals][:4],
                "text": (c.description or "")[:1500],
            }
            for i, c in enumerate(batch)
        ]
        result = _ask(
            client,
            model,
            CLASSIFY_SYSTEM,
            json.dumps({"records": payload}, ensure_ascii=False),
            CLASSIFY_SCHEMA,
        )
        if result is None:
            log.warning("Batch %d fell back to rules", start // BATCH_SIZE)
            kept.extend(_rules_only(batch))
            continue

        by_index = {r["index"]: r for r in result.get("records", [])}
        for i, company in enumerate(batch):
            verdict = by_index.get(i)
            if verdict is None:
                continue
            if not verdict["is_biotech"] or not verdict["is_early_stage"]:
                continue
            company.sector = verdict["sector"]
            company.sector_confidence = float(verdict["confidence"])
            company.summary = verdict["summary"].strip()
            kept.append(company)

    log.info("Classifier kept %d of %d records", len(kept), len(companies))
    return kept


def extract_from_headlines(
    headlines: list[dict], api_key: str | None, model: str
) -> list[Company]:
    """Turn news headlines into Company records. Needs Claude; no regex fallback."""
    if not headlines:
        return []
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — skipping news extraction")
        return []

    client = _client(api_key)
    companies: list[Company] = []

    for start in range(0, len(headlines), BATCH_SIZE * 2):
        batch = headlines[start : start + BATCH_SIZE * 2]
        payload = [
            {"headline_index": i, "headline": h["title"], "outlet": h.get("source", "")}
            for i, h in enumerate(batch)
        ]
        result = _ask(
            client,
            model,
            EXTRACT_SYSTEM,
            json.dumps({"headlines": payload}, ensure_ascii=False),
            EXTRACT_SCHEMA,
        )
        if result is None:
            continue

        for item in result.get("companies", []):
            idx = item.get("headline_index")
            if not isinstance(idx, int) or idx >= len(batch):
                continue
            headline = batch[idx]
            name = item["company_name"].strip()
            if not name:
                continue
            company = Company(
                name=name,
                country=item.get("country") or "unknown",
                sector=item.get("sector", "unknown"),
                sector_confidence=0.6,
                summary=item.get("summary", "").strip(),
                description=headline["title"],
            )
            company.signals.append(
                Signal(
                    source="news",
                    kind="funding_news",
                    title=headline["title"][:300],
                    url=headline["url"],
                    observed_on=headline.get("observed_on") or date.today(),
                    detail=item.get("round_stage", ""),
                )
            )
            companies.append(company)

    log.info("News extraction: %d named companies from %d headlines", len(companies), len(headlines))
    return companies
