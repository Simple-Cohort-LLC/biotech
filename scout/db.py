"""SQLite state. Committed back to the repo so dedup survives across runs."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime

from .models import Company

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    fingerprint       TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    normalized_name   TEXT NOT NULL,
    country           TEXT,
    sector            TEXT,
    summary           TEXT,
    website           TEXT,
    score             INTEGER,
    sources           TEXT,
    first_seen_on     TEXT NOT NULL,
    last_seen_on      TEXT NOT NULL,
    reported_on       TEXT,
    payload           TEXT
);
CREATE INDEX IF NOT EXISTS idx_companies_norm ON companies(normalized_name);
CREATE INDEX IF NOT EXISTS idx_companies_reported ON companies(reported_on);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at        TEXT NOT NULL,
    candidates    INTEGER,
    reported      INTEGER,
    source_counts TEXT,
    notes         TEXT
);
"""


class Store:
    def __init__(self, path: str):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def already_reported(self, fingerprint: str) -> bool:
        row = self.conn.execute(
            "SELECT reported_on FROM companies WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return bool(row and row["reported_on"])

    def upsert(self, company: Company, *, reported: bool) -> None:
        today = date.today().isoformat()
        payload = json.dumps(
            {
                "description": company.description,
                "people": company.people,
                "year_incorporated": company.year_incorporated,
                "offering_usd": company.offering_usd,
                "amount_sold_usd": company.amount_sold_usd,
                "score_reasons": company.score_reasons,
                "signals": [
                    {
                        "source": s.source,
                        "kind": s.kind,
                        "title": s.title,
                        "url": s.url,
                        "observed_on": s.observed_on.isoformat(),
                        "detail": s.detail,
                    }
                    for s in company.signals
                ],
            },
            ensure_ascii=False,
        )
        existing = self.conn.execute(
            "SELECT first_seen_on, reported_on FROM companies WHERE fingerprint = ?",
            (company.fingerprint,),
        ).fetchone()
        first_seen = existing["first_seen_on"] if existing else today
        reported_on = (existing["reported_on"] if existing else None) or (
            today if reported else None
        )

        self.conn.execute(
            """
            INSERT INTO companies (fingerprint, name, normalized_name, country, sector,
                                   summary, website, score, sources, first_seen_on,
                                   last_seen_on, reported_on, payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                name=excluded.name, country=excluded.country, sector=excluded.sector,
                summary=excluded.summary, website=excluded.website, score=excluded.score,
                sources=excluded.sources, last_seen_on=excluded.last_seen_on,
                reported_on=excluded.reported_on, payload=excluded.payload
            """,
            (
                company.fingerprint,
                company.name,
                company.key,
                company.country,
                company.sector,
                company.summary,
                company.website,
                company.score,
                ",".join(sorted(company.sources)),
                first_seen,
                today,
                reported_on,
                payload,
            ),
        )
        self.conn.commit()

    def record_run(
        self, candidates: int, reported: int, source_counts: dict[str, int], notes: str = ""
    ) -> None:
        self.conn.execute(
            "INSERT INTO runs (ran_at, candidates, reported, source_counts, notes) VALUES (?,?,?,?,?)",
            (
                datetime.utcnow().isoformat(timespec="seconds"),
                candidates,
                reported,
                json.dumps(source_counts),
                notes,
            ),
        )
        self.conn.commit()

    def export_csv(self, path: str) -> None:
        """Write a git-diffable snapshot alongside the binary DB."""
        import csv

        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        rows = self.conn.execute(
            """
            SELECT name, country, sector, score, sources, summary, website,
                   first_seen_on, reported_on
            FROM companies ORDER BY first_seen_on DESC, score DESC
            """
        ).fetchall()
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(rows[0].keys() if rows else [
                "name", "country", "sector", "score", "sources", "summary",
                "website", "first_seen_on", "reported_on",
            ])
            for row in rows:
                writer.writerow(list(row))
