"""Entity resolution across sources."""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from .models import Company

log = logging.getLogger(__name__)

# Above this ratio, two normalized names are treated as the same company.
# Tuned conservatively: a false merge silently hides a company, which is worse
# than showing a near-duplicate you can eyeball in two seconds.
FUZZY_THRESHOLD = 0.92
MIN_FUZZY_LENGTH = 8  # short names collide too easily to fuzzy-match


def merge(companies: list[Company]) -> list[Company]:
    exact: dict[str, Company] = {}

    for company in companies:
        key = company.key
        if not key:
            continue
        if key in exact:
            exact[key].merge(company)
            continue

        matched = None
        if len(key) >= MIN_FUZZY_LENGTH:
            for existing_key, existing in exact.items():
                if len(existing_key) < MIN_FUZZY_LENGTH:
                    continue
                # Cheap length gate before the O(n*m) ratio computation.
                if abs(len(existing_key) - len(key)) > 6:
                    continue
                if SequenceMatcher(None, existing_key, key).ratio() >= FUZZY_THRESHOLD:
                    matched = existing
                    break

        if matched is not None:
            matched.merge(company)
        else:
            exact[key] = company

    merged = list(exact.values())
    log.info("Dedup: %d records -> %d companies", len(companies), len(merged))
    return merged
