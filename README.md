# Biotech Scout

A weekly pipeline that surfaces **early-stage (pre–Series A) biotech companies**
from free public sources, classifies them by sector, scores them for signal
quality, and posts a formatted digest to Slack with an attached PDF. Built for
personal research and angel-investing deal flow. Runs on a GitHub Action; no
paid data subscriptions.

## What it does

Each week it pulls from several free sources, resolves the same company appearing
in more than one, uses Claude to classify each into a sector and write a one-line
summary, scores every candidate, and reports only the ones that clear a
high-precision bar.

### Sources

| Source | Signal | Coverage |
|---|---|---|
| **SEC Form D** | New US private raises (filed within 15 days of first sale) | US — the spine |
| **NIH RePORTER** | SBIR/STTR grants (non-dilutive validation) | US |
| **bioRxiv / medRxiv** | Company-affiliated preprints (catches spinouts pre-raise) | Global |
| **UK Companies House** | New biotech incorporations (needs a free API key) | UK |
| **EU CORDIS** | Horizon Europe / EIC grant recipients | EU |
| **Google News RSS** | Funding announcements the structured sources miss | Global |

Coverage is deliberately lopsided toward the US, because that is where the best
free *structured* data lives. The UK and EU are partially covered; everywhere
else shows up only when a preprint or news story happens to surface a company.
That is a property of what is free, not a bug.

### Why these and not others

`llms.txt` and MCP were considered and dropped: both require already knowing a
company exists (you fetch them from the company's own domain), so they are
enrichment signals, not discovery sources — and adoption among seed-stage
biotechs is near zero today. News, preprints, grants, and filings are what
actually surface new names.

## How filtering works (the "high precision" bar)

Companies are ranked by a **signal score** ([scout/score.py](scout/score.py)),
not a quality judgment. Two ways to clear the default threshold of 55:

1. **Multi-source corroboration** — the same company in two independent sources
   in one week is almost never noise.
2. **One strong single signal** — e.g. a first-time Form D from a recently
   incorporated issuer raising a seed-sized round (the canonical pre-A event).

A lone incorporation, a lone preprint, or a lone headline is recorded in the
database but does **not** reach the digest. Investment funds and SPVs are dropped
at the source (Form D pooled-investment filings are excluded outright).

Tune without touching code via env vars: `MIN_SCORE`, `MAX_OFFERING_USD`,
`MAX_COMPANY_AGE_YEARS`, `LOOKBACK_DAYS`, `MAX_DIGEST_ITEMS`.

## Classification

Each candidate is classified by the Claude API into one of nine sectors
(therapeutics, diagnostics, tools & instruments, synthetic biology, techbio,
ag/food, industrial, medtech, digital health) with a one-line summary written
for a reader deciding whether to spend ten minutes on the company. At this
volume the API cost is a few cents per week.

If `ANTHROPIC_API_KEY` is missing or the API errors, classification **falls back
automatically** to a keyword rulebook ([scout/taxonomy.py](scout/taxonomy.py)) —
the run still produces a digest, just with coarser sectors and no news coverage
(news extraction needs the LLM). You get a degraded report, never a silent
failure.

## Setup

### 1. Slack app (required for the PDF)

Slack incoming webhooks **cannot upload files**, so the PDF needs a bot token.

1. Create an app at <https://api.slack.com/apps> → *From scratch*.
2. **OAuth & Permissions** → add bot scopes: `chat:write` and `files:write`.
3. Install to your workspace, copy the **Bot User OAuth Token** (`xoxb-…`).
4. Invite the bot to your channel: `/invite @your-app`.
5. Copy the channel ID (channel details → bottom of the About tab).

A webhook fallback exists (`SLACK_WEBHOOK_URL`) — it posts the digest text but
skips the PDF.

### 2. GitHub repository secrets

Settings → Secrets and variables → Actions:

| Secret | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | recommended | Classification + news extraction. Omit to use the rules fallback. |
| `SLACK_BOT_TOKEN` | yes | `xoxb-…` |
| `SLACK_CHANNEL` | yes | Channel ID, e.g. `C0123456789` |
| `SEC_USER_AGENT` | recommended | `your-project your-email@example.com` — SEC requires a contact UA |
| `COMPANIES_HOUSE_API_KEY` | optional | Free from the [CH developer hub](https://developer.company-information.service.gov.uk/). Omit to skip UK. |

### 3. Schedule

The Action ([.github/workflows/weekly-scout.yml](.github/workflows/weekly-scout.yml))
runs Mondays at 13:00 UTC and can be triggered manually from the Actions tab
(with a dry-run toggle). It commits the SQLite state (`data/scout.db`) and a
diffable `data/companies.csv` back to the repo so deduplication and
already-reported tracking survive across weeks.

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Dry run: no Slack, no DB writes, prints the digest and writes the PDF to out/
python -m scout.main --dry-run --verbose

# Narrow the scope while testing
python -m scout.main --dry-run --lookback-days 3 --min-score 40

# Disable individual sources with env vars
ENABLE_SEC=false python -m scout.main --dry-run
```

Env flags: `ENABLE_SEC`, `ENABLE_NIH`, `ENABLE_BIORXIV`,
`ENABLE_COMPANIES_HOUSE`, `ENABLE_CORDIS`, `ENABLE_NEWS`.

## Layout

```
scout/
  main.py           orchestration: collect → extract → classify → dedup → score → report
  config.py         env-driven configuration
  models.py         the Company/Signal records + name normalization
  http.py           shared session: retries, backoff, global rate limit
  db.py             SQLite state (committed to the repo)
  taxonomy.py       keyword rulebook (fallback classifier — edit to tune)
  classify.py       Claude classification + news name-extraction
  dedup.py          cross-source entity resolution
  score.py          the signal-scoring rubric
  report_pdf.py     the weekly PDF
  slack.py          Block Kit digest + threaded PDF upload
  sources/          one module per source
```

## Caveats

- **This is deal-flow surfacing, not diligence.** A high score means several
  public sources agree a young biotech-shaped company did something this week —
  it says nothing about whether the science or team is good. Every claim links
  to its primary source; check it before acting.
- **Sector labels and summaries are model-generated** from often-thin source
  text and can be wrong.
- **CORDIS is the least stable source.** Its public search endpoint is not
  versioned; the module fails soft if the shape changes (logs a warning, skips
  EU that week). If EU flow matters and it goes quiet, fall back to the
  [CORDIS bulk export](https://cordis.europa.eu/data/).
- **This is not investment advice.**

## License

MIT — see [LICENSE](LICENSE). The data this tool retrieves comes from public
sources, each with its own terms; you are responsible for using them within
those terms. In particular, Google News RSS is convenient but not a formally
supported API — treat the news source as best-effort, and swap in a licensed
feed if you need a durable guarantee.
