"""Google News RSS — funding announcements the structured sources miss.

This is how a Singaporean synbio seed round or a Nordic diagnostics pre-seed
shows up at all: no free structured source covers them. It is also by far the
noisiest input here, so it feeds the extractor rather than the pipeline
directly — the company name is pulled out of the headline by Claude in
classify.py, and a news-only company can never clear the score gate alone.
"""

from __future__ import annotations

import logging
from datetime import date

import feedparser
import requests

from ..http import get

log = logging.getLogger(__name__)

RSS_URL = "https://news.google.com/rss/search"

QUERIES = [
    '"seed round" biotech',
    '"pre-seed" biotech',
    '"seed funding" therapeutics',
    '"seed round" diagnostics',
    '"seed financing" biotech startup',
    '"emerges from stealth" biotech',
    '"launches with" million therapeutics',
    '"raises" "seed" "synthetic biology"',
    '"seed round" "drug discovery"',
    '"spinout" university biotech raises',
]


def fetch_headlines(session: requests.Session, lookback_days: int) -> list[dict]:
    """Return raw headline dicts. Company extraction happens in classify.py."""
    seen_links: set[str] = set()
    headlines: list[dict] = []

    for query in QUERIES:
        params = {
            "q": f"{query} when:{lookback_days}d",
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }
        try:
            resp = get(session, RSS_URL, params=params)
        except requests.RequestException as exc:
            log.warning("Google News fetch failed for %r: %s", query, exc)
            continue
        if not resp.ok:
            log.warning("Google News returned %s for %r", resp.status_code, query)
            continue

        feed = feedparser.parse(resp.content)
        for entry in feed.entries:
            link = getattr(entry, "link", "")
            title = getattr(entry, "title", "")
            if not link or not title or link in seen_links:
                continue
            seen_links.add(link)
            headlines.append(
                {
                    "title": title,
                    "url": link,
                    "source": getattr(getattr(entry, "source", None), "title", ""),
                    "published": getattr(entry, "published", ""),
                    "observed_on": date.today(),
                }
            )

    log.info("Google News: %d unique headlines", len(headlines))
    return headlines
