# cofair-colonial

**List-price pipeline for the COFAIR platform.** Scrapes Anthropic, OpenAI, and Google Vertex list prices, commits dated snapshots under `pricing_history/`, and optionally loads them into Postgres/Neon for analytics.

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

---

## `pricing_history/` (exchange contract)

Each file is `pricing_history/YYYY-MM-DD.json` with:

- `meta.schema_version` (e.g. `2.1.0`)
- `providers[]` — `provider_id`, `name`
- `pricing[]` — `pricing_id`, `model_id`, `input_price` / `output_price` per 1M tokens

Exchange matches `occurred_at` → snapshot date → `(provider_id, model_id)` — see platform [PRICING.md](https://github.com/Climate-Offsets-for-AI-Responsibility/cofair-platform/blob/main/PRICING.md).

---

## Scheduled updates

`.github/workflows/daily-scrape.yml` — daily scrape, Neon ingest, commit `pricing.json` + `pricing_history/` when changed.

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
