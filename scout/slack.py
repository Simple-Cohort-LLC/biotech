"""Slack delivery: a Block Kit digest, with the PDF threaded under it.

Uses a bot token. Note that Slack's incoming webhooks cannot upload files at
all — if only SLACK_WEBHOOK_URL is set, we post the digest text and skip the
PDF rather than failing silently.

Required bot scopes: chat:write, files:write.
The file upload uses the external-upload flow (getUploadURLExternal ->
PUT bytes -> completeUploadExternal); the old files.upload endpoint is retired.
"""

from __future__ import annotations

import logging
import os
from datetime import date

import requests

from .http import get, post
from .models import Company

log = logging.getLogger(__name__)

API = "https://slack.com/api"

SECTOR_EMOJI = {
    "therapeutics": ":pill:",
    "diagnostics": ":microscope:",
    "tools-and-instruments": ":wrench:",
    "synthetic-biology": ":dna:",
    "techbio-and-bioinformatics": ":computer:",
    "ag-and-food-tech": ":seedling:",
    "industrial-biotech": ":factory:",
    "medtech-and-devices": ":stethoscope:",
    "digital-health": ":iphone:",
    "unknown": ":grey_question:",
}


def _headline(companies: list[Company], source_counts: dict[str, int]) -> str:
    if not companies:
        return "No companies cleared the signal threshold this week."
    sectors = {}
    for c in companies:
        sectors[c.sector] = sectors.get(c.sector, 0) + 1
    top = ", ".join(f"{n} {s.replace('-', ' ')}" for s, n in
                    sorted(sectors.items(), key=lambda kv: -kv[1])[:3])
    return f"{len(companies)} companies cleared the threshold — {top}."


def _blocks(companies: list[Company], source_counts: dict[str, int], pdf_note: str) -> list[dict]:
    today = date.today()
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Biotech Scout — {today.strftime('%b %d, %Y')}"},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"{_headline(companies, source_counts)}  ·  Scanned: "
                        + ", ".join(f"{k} ({v})" for k, v in sorted(source_counts.items()))
                    ),
                }
            ],
        },
        {"type": "divider"},
    ]

    if not companies:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Nothing to report.* A quiet week is a normal outcome for a "
                        "high-precision filter — the sources published, nothing cleared "
                        "the bar. No action needed."
                    ),
                },
            }
        )
        return blocks

    # Slack caps a message at 50 blocks; each company costs one, so show the top
    # dozen inline and let the PDF carry the rest.
    for company in companies[:12]:
        emoji = SECTOR_EMOJI.get(company.sector, ":grey_question:")
        primary = company.signals[0] if company.signals else None
        link = f"<{primary.url}|{company.name}>" if primary else company.name
        detail_bits = [company.sector.replace("-", " "), company.country]
        if company.offering_usd:
            detail_bits.append(f"${company.offering_usd:,} offering")
        if company.year_incorporated:
            detail_bits.append(f"inc. {company.year_incorporated}")
        detail_bits.append(f"score {company.score}")

        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji}  *{link}*\n"
                        f"{company.summary or '_No description available from source data._'}\n"
                        f"_{' · '.join(detail_bits)}_"
                    ),
                },
            }
        )

    if len(companies) > 12:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"_+{len(companies) - 12} more in the attached PDF._",
                    }
                ],
            }
        )

    blocks.append({"type": "divider"})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": pdf_note}]})
    return blocks


def _upload_pdf(
    session: requests.Session, token: str, channel_id: str, thread_ts: str, pdf_path: str
) -> bool:
    filename = os.path.basename(pdf_path)
    size = os.path.getsize(pdf_path)
    headers = {"Authorization": f"Bearer {token}"}

    resp = get(
        session,
        f"{API}/files.getUploadURLExternal",
        headers=headers,
        params={"filename": filename, "length": size},
    )
    body = resp.json() if resp.ok else {}
    if not body.get("ok"):
        log.error("Slack getUploadURLExternal failed: %s", body.get("error", resp.status_code))
        return False

    upload_url, file_id = body["upload_url"], body["file_id"]

    with open(pdf_path, "rb") as fh:
        put = post(session, upload_url, files={"file": (filename, fh, "application/pdf")})
    if not put.ok:
        log.error("Slack file bytes upload failed: %s", put.status_code)
        return False

    complete = post(
        session,
        f"{API}/files.completeUploadExternal",
        headers={**headers, "Content-Type": "application/json; charset=utf-8"},
        json={
            "files": [{"id": file_id, "title": f"Biotech Scout — {date.today().isoformat()}"}],
            "channel_id": channel_id,
            "thread_ts": thread_ts,
        },
    )
    done = complete.json() if complete.ok else {}
    if not done.get("ok"):
        log.error("Slack completeUploadExternal failed: %s", done.get("error", complete.status_code))
        return False
    return True


def deliver(
    session: requests.Session,
    companies: list[Company],
    pdf_path: str | None,
    source_counts: dict[str, int],
    token: str | None,
    channel: str | None,
    webhook_url: str | None,
) -> bool:
    pdf_note = (
        ":page_facing_up: Full report attached in thread — links and summaries for every company."
        if pdf_path
        else ":warning: PDF not generated this run."
    )

    if not token or not channel:
        if webhook_url:
            log.warning(
                "No bot token/channel — falling back to the webhook. "
                "Slack webhooks cannot upload files, so the PDF will not be attached."
            )
            resp = post(
                session,
                webhook_url,
                json={
                    "text": _headline(companies, source_counts),
                    "blocks": _blocks(companies, source_counts, pdf_note),
                },
            )
            return resp.ok
        log.error("No Slack credentials configured — nothing delivered")
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    resp = post(
        session,
        f"{API}/chat.postMessage",
        headers=headers,
        json={
            "channel": channel,
            "text": _headline(companies, source_counts),  # notification fallback text
            "blocks": _blocks(companies, source_counts, pdf_note),
            "unfurl_links": False,
        },
    )
    body = resp.json() if resp.ok else {}
    if not body.get("ok"):
        log.error("Slack chat.postMessage failed: %s", body.get("error", resp.status_code))
        return False

    log.info("Posted digest to %s", channel)

    if pdf_path:
        uploaded = _upload_pdf(session, token, body["channel"], body["ts"], pdf_path)
        if not uploaded:
            log.error("Digest posted but PDF upload failed — check the files:write scope")
            return False
    return True
