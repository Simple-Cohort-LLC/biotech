"""Entry point: collect -> extract -> classify -> dedup -> score -> report."""

from __future__ import annotations

import argparse
import logging
import sys

from . import classify as classifier
from . import dedup, report_pdf, score, slack
from .config import CONFIG, Config
from .db import Store
from .http import build_session
from .models import Company
from .sources import biorxiv, companies_house, cordis, news, nih, sec_formd, yc

log = logging.getLogger("scout")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def collect(session, config: Config) -> tuple[list[Company], list[dict], dict[str, int]]:
    """Run every enabled source. A source that fails is logged and skipped."""
    raw: list[Company] = []
    headlines: list[dict] = []
    counts: dict[str, int] = {}

    sources = [
        ("sec_form_d", config.enable_sec, lambda: sec_formd.fetch(session, config.lookback_days)),
        ("nih_reporter", config.enable_nih, lambda: nih.fetch(session, config.lookback_days)),
        ("preprints", config.enable_biorxiv, lambda: biorxiv.fetch(session, config.lookback_days)),
        (
            "companies_house",
            config.enable_companies_house,
            lambda: companies_house.fetch(session, config.lookback_days, config.companies_house_key),
        ),
        ("cordis", config.enable_cordis, lambda: cordis.fetch(session, config.lookback_days)),
        (
            "yc",
            config.enable_yc,
            lambda: yc.fetch(session, config.yc_batch_max_age_years),
        ),
    ]

    for name, enabled, run in sources:
        if not enabled:
            log.info("%s: disabled", name)
            continue
        try:
            found = run()
        except Exception as exc:  # noqa: BLE001 - one bad source must not kill the run
            log.exception("%s failed, continuing without it: %s", name, exc)
            counts[name] = 0
            continue
        counts[name] = len(found)
        raw.extend(found)

    if config.enable_news:
        try:
            headlines = news.fetch_headlines(session, config.lookback_days)
            counts["news_headlines"] = len(headlines)
        except Exception as exc:  # noqa: BLE001
            log.exception("news failed, continuing without it: %s", exc)
            counts["news_headlines"] = 0

    return raw, headlines, counts


def run(config: Config) -> int:
    session = build_session(config.sec_user_agent)
    store = Store(config.db_path)

    try:
        raw, headlines, counts = collect(session, config)

        from_news = classifier.extract_from_headlines(
            headlines, config.anthropic_api_key, config.classifier_model
        )
        counts["news_companies"] = len(from_news)
        raw.extend(from_news)

        if not raw:
            log.warning("No candidates from any source this run")

        # Dedup before classifying: merging first means one Claude call per
        # company instead of one per source hit, and gives the classifier the
        # union of all the text we have about each company.
        merged = dedup.merge(raw)
        classified = classifier.classify(
            merged, config.anthropic_api_key, config.classifier_model
        )
        ranked = score.rank(classified, config)

        fresh = [
            c
            for c in ranked
            if c.score >= config.min_score and not store.already_reported(c.fingerprint)
        ]
        dropped_low = sum(1 for c in ranked if c.score < config.min_score)
        dropped_seen = len(ranked) - len(fresh) - dropped_low

        digest = fresh[: config.max_digest_items]
        if len(fresh) > len(digest):
            log.warning(
                "Capped digest at %d of %d qualifying companies — %d are recorded in "
                "the database but not reported this week",
                len(digest), len(fresh), len(fresh) - len(digest),
            )

        log.info(
            "Pipeline: %d raw -> %d merged -> %d classified -> %d qualifying "
            "(%d below score %d, %d already reported) -> %d in digest",
            len(raw), len(merged), len(classified), len(fresh),
            dropped_low, config.min_score, dropped_seen, len(digest),
        )

        pdf_path = report_pdf.build(digest, config.out_dir, counts)
        log.info("PDF written to %s", pdf_path)

        if config.dry_run:
            log.info("DRY_RUN set — skipping Slack delivery and database writes")
            for company in digest:
                log.info("  [%3d] %-45s %-28s %s", company.score, company.name[:45],
                         company.sector, company.summary[:60])
            return 0

        delivered = slack.deliver(
            session,
            digest,
            pdf_path,
            counts,
            config.slack_bot_token,
            config.slack_channel,
            config.slack_webhook_url,
        )

        # Mark as reported only if Slack actually accepted it. If delivery
        # failed, next week's run should surface these companies again rather
        # than swallowing them.
        for company in ranked:
            store.upsert(company, reported=delivered and company in digest)
        store.record_run(
            candidates=len(ranked),
            reported=len(digest) if delivered else 0,
            source_counts=counts,
            notes="" if delivered else "slack delivery failed",
        )
        store.export_csv("data/companies.csv")

        if not delivered:
            log.error("Slack delivery failed — companies left unreported for next run")
            return 1
        return 0
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Early-stage biotech source scout")
    parser.add_argument("--dry-run", action="store_true", help="Skip Slack and DB writes")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--lookback-days", type=int, help="Override the lookback window")
    parser.add_argument("--min-score", type=int, help="Override the digest score threshold")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    config = CONFIG
    if args.dry_run:
        config.dry_run = True
    if args.lookback_days:
        config.lookback_days = args.lookback_days
    if args.min_score is not None:
        config.min_score = args.min_score

    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
