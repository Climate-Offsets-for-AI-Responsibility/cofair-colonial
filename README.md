# cofair-colonial

**List-price pipeline for the COFAIR platform.** Scrapes Anthropic, OpenAI, Google Vertex, xAI, DeepSeek, and Qwen list prices, commits dated snapshots under `pricing_history/`, and optionally loads them into Postgres/Neon for analytics.

| Consumer | How it uses this repo |
|----------|------------------------|
| **`cofair-exchange`** | Ingests `pricing_history/YYYY-MM-DD.json` → `resolvePrice()` for attribution ([PRICING.md](https://github.com/Climate-Offsets-for-AI-Responsibility/cofair-platform/blob/main/PRICING.md)) |
| **Ops / data** | Daily GitHub Action, dbt staging views, optional dashboard |

**Former name:** `the-colonial` (GitHub redirects after rename).

**Platform docs:** [cofair-platform](https://github.com/Climate-Offsets-for-AI-Responsibility/cofair-platform) · [ARCHITECTURE](https://github.com/Climate-Offsets-for-AI-Responsibility/cofair-platform/blob/main/ARCHITECTURE.md)

---

## Clone

```bash
cd ~/Documents/GitHub   # or ~/Github
git clone git@github.com:Climate-Offsets-for-AI-Responsibility/cofair-colonial.git
```

Local dev for the full stack: set `COLONIAL_PRICING_DIR` in `cofair/.env/.env.cofair`:

```bash
COLONIAL_PRICING_DIR=../cofair-colonial/pricing_history
```

---

## Setup

Run commands from the **`cofair-colonial`** directory.

Create `.env` from `.env.example` (Postgres Docker, optional Neon/Slack for CI).

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/), then:

```bash
open -a Docker
docker compose up -d
docker ps
```

---

## Run pipeline locally

```bash
python3 build_db.py
```

- Runs `scrape_pricing.py` → ingests pricing → usage (dataclaw) → dbt `staging.stg_pricing` / `stg_usage`
- For Neon: `python3 build_db.py --neon` (requires `NETLIFY_DATABASE_URL_UNPOOLED`)

Manual scrape only:

```bash
python3 scrape_pricing.py
```

## Token equivalence live-run credentials

Pricing index scraping is public-page based and does not require provider accounts.

The token-equivalence dashboard runs a **recommended ~$40–45/yr** package:

- Weekly **task meter** (A–D): flagship N=1, workhorse N=3 (median)
- Daily **tokenizer ledger** (A+B+C count-only); weekly ledger count for D
- Weekly **Test 4** wrapper overhead on a frozen 10-turn transcript (turns 1–10)

Set these environment variables in `.env` (or `../cofair/.env/.env.cofair`).
Prefer the `TRACKER_*` names in the shared hub env so they do not collide with
exchange / M0 tracer keys; unprefixed names still work for CI and a local `.env`:

- `TRACKER_ANTHROPIC_API_KEY` (or `ANTHROPIC_API_KEY`)
- `TRACKER_OPENAI_API_KEY` (or `OPENAI_API_KEY`)
- `TRACKER_GEMINI_API_KEY` (or `GEMINI_API_KEY` / `GOOGLE_API_KEY`)
- `TRACKER_XAI_API_KEY` (or `XAI_API_KEY`)
- `TRACKER_DEEPSEEK_API_KEY` (or `DEEPSEEK_API_KEY`)
- `TRACKER_QWEN_API_KEY` (or `QWEN_API_KEY`)
- `TRACKER_AMAZON_API_KEY` (Bedrock API key / ABSK…; or `AWS_BEARER_TOKEN_BEDROCK`)
- optional `BEDROCK_REGION` (default `us-east-1`)

`build_dashboard_data.py` and the runners auto-load `../cofair/.env/.env.cofair` when present.

```bash
# Daily meter (one replicate per tier, the shipped schedule)
python3 scripts/run_equivalence_tasks.py --mode two --workhorse-replicates 1

# Daily ledger (ABC) / weekly ledger (D)
python3 scripts/run_tokenizer_ledger.py --tasks ABC --mode two
python3 scripts/run_tokenizer_ledger.py --tasks D --mode two

# Test 4 wrapper counts
python3 scripts/run_wrapper_overhead.py --mode two --max-turn 10

# Dry-run (no provider calls)
python3 scripts/run_equivalence_tasks.py --mode two --dry-run
python3 scripts/run_tokenizer_ledger.py --tasks ABC --dry-run
python3 scripts/run_wrapper_overhead.py --dry-run
```

CI: `.github/workflows/daily-tokenizer-ledger.yml` and `.github/workflows/weekly-token-equivalence.yml`.

### Scrape resilience process

When provider pages redesign (moved tables, renamed columns, new tab markup), `scrape_pricing.py` now does a staged recovery before failing:

1. **Provider-level retry:** each provider parser reruns once when component rows are below a provider floor.
2. **Sanity evaluation:** total + per-provider thresholds are checked against both absolute floors and the prior snapshot ratio.
3. **Fallback remediation:** if a provider collapses, the run attempts to carry forward that provider from the last good `pricing.json` instead of dropping it.
4. **Diagnostics artifact:** `run_report.json` now includes provider component counts, sanity issues, and whether remediation was applied.

If sanity still fails after remediation, the run exits non-zero so CI still alerts.

---

## `pricing_history/` (exchange contract)

Each file is `pricing_history/YYYY-MM-DD.json` with:

- `meta.schema_version` (e.g. `2.1.0`)
- `providers[]` — `provider_id`, `name`
- `pricing[]` — `pricing_id`, `model_id`, `input_price` / `output_price` per 1M tokens

Exchange matches `occurred_at` → snapshot date → `(provider_id, model_id)` — see platform [PRICING.md](https://github.com/Climate-Offsets-for-AI-Responsibility/cofair-platform/blob/main/PRICING.md).

---

## Dashboard

**Public URLs:** [cofair.org/pricing/](https://cofair.org/pricing/) and [cofair.org/tokens/](https://cofair.org/tokens/) — both proxied from the marketing site onto this repo's Netlify publish (`cofair-colonial.netlify.app`). The no-slash forms are answered directly with the page (they do **not** 301), so each page injects `<base href="/…/">` to keep its relative asset paths resolving.

Two things about that proxy are easy to get wrong, both recorded as D63 in the hub:

- **The proxy forwards one path prefix at a time.** `/tokens/` sits a level deeper than the root page and reaches shared assets with `../`, which resolves *above* the forwarded prefix and lands on the marketing site. `cofair-marketing/public/_redirects` therefore also forwards `/vendor/*`, `/styles.css` and `/data/equivalence.json`. Add a rule there before pointing a new page at a shared asset — this failure returns 200 and renders unstyled, and it is invisible when you test against `cofair-colonial.netlify.app` directly.
- **Deploys are continuous from `main`** (the Netlify GitHub App builds on push; publish dir `dashboard`, per `netlify.toml`). This was not always true — deploys used to be manual CLI runs, and a session's work sat unpublished. To publish out of band: `npx netlify-cli deploy --prod --no-build` from the repo root. Never pass the repo root as `--dir`; `pricing_history/` makes it 2.3 GB and the upload 413s. Do not put `[skip ci]` in an automated commit message: Netlify honours it too, so the data commits would land on `main` without ever reaching the published dashboard.

`dashboard/` is a zero-build static page (Netlify publishes it directly) charting the snapshot history. It reads the artifacts in `dashboard/data/`, which `scripts/build_dashboard_data.py` generates:

```bash
python3 scripts/build_dashboard_data.py --rebuild   # from all of pricing_history/
python3 -m http.server 8787 --directory dashboard   # then open http://127.0.0.1:8787
```

Dashboard artifacts intentionally start at `2026-06-17` (`DASHBOARD_START_DATE`) because the earlier archive import does not have reliable snapshot granularity.

The page is built on the **COFAIR design system**. Because it has no bundler, it consumes `@cofair/ui`'s compiled stylesheet and uses the same `cofair-*` classes. Those copies live in `dashboard/vendor/` (stylesheet, IBM Plex subset, brand marks, Chart.js) and are **generated — never edit them**:

```bash
python3 scripts/sync_design_system.py           # refresh from ../cofair-design-system
python3 scripts/sync_design_system.py --check   # exit 1 if a copy is stale
```

Requires `cofair-design-system` as a sibling checkout with `npm install && npm run build` run in it, plus `npm install` here for Chart.js. Fonts and Chart.js are vendored rather than pulled from a CDN so the page makes no third-party requests. Styling rules for this directory are in [`AGENTS.md`](AGENTS.md).

---

## Scheduled updates

`.github/workflows/daily-scrape.yml` — daily scrape, Neon ingest, commit `pricing.json` + `pricing_history/` when changed.

`.github/workflows/weekly-token-equivalence.yml` — weekly A/B/C/D live equivalence run, then rebuild + commit `dashboard/data/equivalence*.json`.

Secrets: `SLACK_*`, `NETLIFY_DATABASE_URL_UNPOOLED`, `NEON_*` (see `.env.example`).

---

## Database layout (local Docker)

```
cofair_db
├── raw.pricing_json
├── raw.usage
├── staging.stg_pricing
└── staging.stg_usage
```

Inspect: `docker compose exec postgres psql -U postgres -d cofair_db`

Notebook: `neon_testing.ipynb` for pandas exploration.

---

## Rename from `the-colonial`

Org admins:

```bash
gh auth login   # COFAIR org account
gh repo rename cofair-colonial --repo Climate-Offsets-for-AI-Responsibility/the-colonial
```

GitHub keeps redirects from the old URL. Update remotes:

```bash
git remote set-url origin git@github.com:Climate-Offsets-for-AI-Responsibility/cofair-colonial.git
```
