"""Runtime configuration, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass
class Config:
    # SEC requires a descriptive User-Agent with a contact address on every
    # request; anonymous traffic gets 403'd. Set SEC_USER_AGENT to your own
    # "project-name your-email@example.com" — the default is a generic
    # placeholder so no personal contact ships in the public repo.
    sec_user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "SEC_USER_AGENT", "biotech-scout contact@example.com"
        )
    )

    lookback_days: int = field(default_factory=lambda: _int("LOOKBACK_DAYS", 8))

    # Anything at or above this offering size is probably past a seed round.
    max_offering_usd: int = field(
        default_factory=lambda: _int("MAX_OFFERING_USD", 30_000_000)
    )
    # Companies incorporated longer ago than this are unlikely to be pre-A.
    max_company_age_years: int = field(
        default_factory=lambda: _int("MAX_COMPANY_AGE_YEARS", 8)
    )
    # Minimum score to appear in the digest. See scout/score.py for the rubric.
    min_score: int = field(default_factory=lambda: _int("MIN_SCORE", 55))
    max_digest_items: int = field(default_factory=lambda: _int("MAX_DIGEST_ITEMS", 25))
    # Include active YC biotech companies from this many prior calendar years.
    # One captures the current cohort plus the immediately preceding batches.
    yc_batch_max_age_years: int = field(
        default_factory=lambda: _int("YC_BATCH_MAX_AGE_YEARS", 1)
    )

    anthropic_api_key: str | None = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY")
    )
    classifier_model: str = field(
        default_factory=lambda: os.environ.get("CLASSIFIER_MODEL", "claude-opus-4-8")
    )

    slack_bot_token: str | None = field(
        default_factory=lambda: os.environ.get("SLACK_BOT_TOKEN")
    )
    slack_channel: str | None = field(
        default_factory=lambda: os.environ.get("SLACK_CHANNEL")
    )
    slack_webhook_url: str | None = field(
        default_factory=lambda: os.environ.get("SLACK_WEBHOOK_URL")
    )

    companies_house_key: str | None = field(
        default_factory=lambda: os.environ.get("COMPANIES_HOUSE_API_KEY")
    )

    db_path: str = field(default_factory=lambda: os.environ.get("DB_PATH", "data/scout.db"))
    out_dir: str = field(default_factory=lambda: os.environ.get("OUT_DIR", "out"))

    dry_run: bool = field(default_factory=lambda: _flag("DRY_RUN", False))

    enable_sec: bool = field(default_factory=lambda: _flag("ENABLE_SEC"))
    enable_nih: bool = field(default_factory=lambda: _flag("ENABLE_NIH"))
    enable_biorxiv: bool = field(default_factory=lambda: _flag("ENABLE_BIORXIV"))
    enable_companies_house: bool = field(
        default_factory=lambda: _flag("ENABLE_COMPANIES_HOUSE")
    )
    enable_cordis: bool = field(default_factory=lambda: _flag("ENABLE_CORDIS"))
    enable_news: bool = field(default_factory=lambda: _flag("ENABLE_NEWS"))
    enable_yc: bool = field(default_factory=lambda: _flag("ENABLE_YC"))


CONFIG = Config()
