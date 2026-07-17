"""The one record type the whole pipeline passes around."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

SECTORS = [
    "therapeutics",
    "diagnostics",
    "tools-and-instruments",
    "synthetic-biology",
    "techbio-and-bioinformatics",
    "ag-and-food-tech",
    "industrial-biotech",
    "medtech-and-devices",
    "digital-health",
    "unknown",
]

# Suffixes and filler words stripped before building the dedup key, so that
# "Acme Therapeutics, Inc." and "Acme Therapeutics Inc" collapse to one company.
_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|llc|l\.l\.c|ltd|limited|plc|gmbh|ag|"
    r"sa|s\.a|nv|b\.?v|oy|ab|aps|as|pte|pty|holdings?|group)\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Collapse a company name to a stable dedup key."""
    lowered = name.lower().strip()
    lowered = _LEGAL_SUFFIXES.sub(" ", lowered)
    lowered = _NON_ALNUM.sub(" ", lowered)
    return " ".join(lowered.split())


@dataclass
class Signal:
    """One piece of evidence that a company exists and is doing something."""

    source: str  # "sec_form_d", "nih_reporter", "biorxiv", ...
    kind: str  # "form_d_filing", "sbir_phase_1", "preprint", "news", ...
    title: str
    url: str
    observed_on: date
    detail: str = ""


@dataclass
class Company:
    name: str
    country: str = "US"
    signals: list[Signal] = field(default_factory=list)

    description: str = ""
    website: str | None = None
    sector: str = "unknown"
    sector_confidence: float = 0.0
    summary: str = ""  # one-liner for the digest, written by the classifier

    year_incorporated: int | None = None
    offering_usd: int | None = None
    amount_sold_usd: int | None = None
    people: list[str] = field(default_factory=list)
    is_first_form_d: bool | None = None

    score: int = 0
    score_reasons: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return normalize_name(self.name)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.key.encode()).hexdigest()[:16]

    @property
    def sources(self) -> set[str]:
        return {s.source for s in self.signals}

    def merge(self, other: "Company") -> None:
        """Fold another record for the same company into this one."""
        self.signals.extend(other.signals)
        if len(other.name) > len(self.name):
            # Prefer the longer rendering; it usually carries the legal suffix.
            self.name = other.name
        for attr in (
            "description",
            "website",
            "year_incorporated",
            "offering_usd",
            "amount_sold_usd",
        ):
            if getattr(self, attr) in (None, "") and getattr(other, attr) not in (None, ""):
                setattr(self, attr, getattr(other, attr))
        if other.country and other.country != "US" and self.country == "US":
            self.country = other.country
        for person in other.people:
            if person not in self.people:
                self.people.append(person)
        if self.is_first_form_d is None:
            self.is_first_form_d = other.is_first_form_d
