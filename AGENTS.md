# AGENTS.md — cofair-colonial

**Project state and rules live in the COFAIR hub, not here.** Before working:
- Source of truth: [`../cofair/docs/pm/TASKS.md`](../cofair/docs/pm/TASKS.md).
- Rules + decisions: [`../cofair/docs/pm/TECHNICAL_DECISIONS.md`](../cofair/docs/pm/TECHNICAL_DECISIONS.md) (R1–R16, esp. R8 + D5).
- Valuation spec: [`../cofair/PRICING.md`](../cofair/PRICING.md).

## This repo's role
The **upstream list-price scraper**. Produces `pricing_history/YYYY-MM-DD.json` (the pricing contract `cofair-exchange` consumes). Python (Playwright/BeautifulSoup/pandas) + dbt + Neon + a daily GitHub Action + Netlify functions. Mature; not a product app.

## Rules that bind this repo (most-violated first)
- **The snapshot JSON is a contract (R8).** `pricing[]` rows carry `pricing_id`, `provider_id`, `model_id`, `input_price`, `output_price` (per 1M tokens) and `meta.schema_version`. The exchange freezes these onto immutable attribution line items — **do not change the snapshot shape without a `schema_version` bump** and a heads-up to the exchange (P1).
- **Stay upstream.** COFAIR pricing logic lives in `cofair-exchange/src/pricing/colonial.ts` (it only *consumes* snapshots). Don't fork valuation logic here, and don't add COFAIR business rules (fees, billing) to this repo.
- Keep the daily scrape healthy; snapshots committed and dated.

## Session checks
Start: read the task + R-rules. End: write back to `../cofair/docs/pm/TASKS.md` if this affected a COFAIR task; commit clearly.

## Coexistence
Macro PM = the hub system. In-task craft = superpowers skills.
