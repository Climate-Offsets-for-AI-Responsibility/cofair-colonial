# Token Costs and Corpus Normalization

Date: 2026-09-03  
Status: Approved design

## Goal

Make `/tokens` tell one consistent story:

- one canonical A–F corpus across selectors, tables, trend totals, and costs;
- task E behaves like the other generating tasks rather than being a separate
  count-only exception;
- the trends surface contains only consumer-facing billed-token measures;
- explanations sit below the chart instead of crowding the selectors; and
- a new Costs tab reports auditable, usage-priced estimates for every daily
  token workflow request.

The displayed record for both `/pricing` and `/tokens` restarts on 2026-09-03.
Raw append-only history is retained.

## Corpus normalization

### Canonical task sets

Define task sets by purpose rather than deriving one semantic set from another:

- `TASK_IDS = ("A", "B", "C", "D", "E", "F")`
- `GENERATING_TASK_IDS = TASK_IDS`
- `LEDGER_TASK_IDS = ("A", "B", "C", "D", "F")`

The tokenizer ledger excludes E because E is a conversation, not one frozen
text prompt. The generation meter includes all six.

`TASK_PACKS["suiteLong"]` becomes A–F. The visible label is `Full suite (A–F)`.
The task selector contains individual A, B, C, D, E, and F options in that
order. No label or badge calls E “count-only.”

The Task Corpus table reads the selected pack directly, not the ledger or meter
subset. Full suite therefore shows all six rows; selecting E shows E.

### Task E: three-turn generated conversation

Replace the frozen ten-turn count-only transcript with a three-turn generating
task. User prompts are frozen and relational:

1. “An organization uses AI for both routine administrative work and
   high-stakes analysis. Explain how it should decide when to use a flagship
   model and when to use a workhorse model.”
2. “Challenge your recommendation from the perspective of a team that values
   reliability more than cost.”
3. “Now revise the recommendation to address that challenge while keeping
   spending predictable. End with a practical three-step policy.”

Each model receives turn 1, its own assistant response remains in context for
turn 2, and both prior responses remain in context for turn 3. The three
requests’ observed input and output tokens are summed into E’s daily meter row.
This intentionally measures compounding conversational usage: provider
verbosity increases both current output and later input.

Each turn is also retained as an individual cost event. Sampling follows the
same deterministic settings and uncapped output policy as A–D/F. The prompt
requests bounded structure but the harness does not enforce an output ceiling.

Increment `CHAT_CORPUS_VERSION` and `CORPUS_VERSION` deliberately. Existing E
and A–F raw rows remain append-only. Trend lines break when their corpus basis
changes, using the existing segmentation mechanism.

The old wrapper workflow and UI aggregation become historical only; no new
wrapper rows are scheduled after E moves into the meter.

## Epoch

Set the shared dashboard presentation floor to 2026-09-03 for both pricing and
tokens. Apply it to pricing trends, price changes, archive, token trends,
ledger, and costs. Do not delete or rewrite source snapshots or raw run files.

The Costs tab has a stricter completeness floor: it begins on the first
2026-09-03 run produced after request-level cost instrumentation ships. A
manual workflow dispatch creates that complete baseline. Earlier September 3
rows that cannot be reconstructed completely are not mixed into cost totals.

## Trends surface

The public Measure selector contains:

1. Total tokens billed
2. Output tokens
3. Input tokens
4. Fixed request overhead

Remove Content density. Remove Cost per run from the trends selector, including
the `?internal` variant; monetary analysis belongs in Costs.

Move `#metricNote` below the chart card. It continues to change with the active
measure. In particular, the fixed-overhead description appears below the chart,
not adjacent to the dropdown.

Remove the visible health sentence beginning “Not collecting for this
measure.” Provider health still drives chip styling and accessible titles, and
the workflow verification gate still fails or degrades appropriately. Removing
the prose does not remove operational health checks.

## Cost event model

Add an append-only cost-event artifact. One successful provider request creates
one event:

```text
date
run_at
source                 meter | ledger
provider_id
tier
task_id
turn                    null for single-turn tasks
request_kind            generation | completion_probe | count_endpoint
api_model
input_tokens
output_tokens
input_price_per_1m
output_price_per_1m
input_cost_usd
output_cost_usd
estimated_cost_usd
pricing_snapshot_date
corpus_version
chat_corpus_version
run_id
```

`estimated_cost_usd` is usage multiplied by the same-day published rates frozen
for the served model. The UI consistently calls it **Estimated spend**, not an
invoice or an actual provider charge.

For generation and completion-probe requests, retain the observed input and
output usage returned by the provider. For provider-native token-count
endpoints documented as non-billable, record a zero estimated cost with
`request_kind=count_endpoint`; do not pretend that priced input inference
occurred. If a path’s billing behavior or usage is unknown, write an incomplete
event with no cost. A daily run containing an expected incomplete event is not
eligible for a complete total.

Rejected model-cap discovery probes and failed HTTP attempts expose no usage
and are not assigned a fabricated charge. Record their operational attempt
elsewhere as today, but exclude them from usage-priced spend. Successful
retried requests produce one cost event from their returned usage.

Cost events use deterministic identities derived from run/source/provider/tier/
task/turn, so workflow replay replaces the same logical observation rather than
double-counting it.

## Cost aggregation

The dashboard builder publishes derived daily cost summaries from complete cost
events. A complete date must contain:

- every selected provider × tier panel row;
- generation events for A, B, C, D, F;
- all three generation turns for E; and
- the expected tokenizer-ledger events for A, B, C, D, F.

Incomplete dates remain visible as incomplete but are not presented as a full
daily spend.

### Comparison periods

- **Current Run:** latest attempted complete run versus the immediately
  preceding complete run.
- **Current Month:** calendar month-to-date through the latest run date versus
  the previous calendar month from day 1 through the same day number, clamped
  to the prior month’s length.
- **Year to Date:** January 1 through the latest run date versus the prior year
  from January 1 through the equivalent month and day.

Each comparison displays current estimated spend, percentage delta, and
absolute dollar delta. If the comparison period is absent, incomplete, or
zero-valued, display `Comparison unavailable` (or `New baseline` for a valid
zero baseline) rather than inventing a percentage.

Month and year comparisons require every scheduled date in both compared
windows to be complete. This prevents a provider outage from appearing as cost
improvement.

## Costs tab UI

Add a third ARIA tab after Ledger: **Costs**. Existing click and left/right
arrow behavior extends to all three tabs.

At the top, render three equal-width existing COFAIR Card treatments:

- Current Run
- Current Month
- Year to Date

Cards wrap to one column at narrow widths. Each card contains the current
estimated spend and one signed comparison line, for example
`+8.2% · +$0.0142`. Positive drift is visually cautionary, negative drift is
favorable, and neutral/unavailable states do not rely on color alone.

Below the cards:

- a `Run date` selector defaults to the latest complete date;
- a table contains one parent row per provider × tier;
- parent rows show input cost, output cost, supporting-call cost, and total;
- activating a parent row expands task rows A–F;
- task rows may expand again into source/turn detail when more than one request
  contributed, especially E’s three turns.

Expansion uses buttons with `aria-expanded` and keyboard-operable table rows.
Values use adaptive precision: two decimals at or above $1, four below $1, and
six below one cent. Totals are calculated from unrounded event values.

The tab includes a concise note: “Estimated spend = observed usage × the
same-day published rate for the served model. It may differ from provider
invoices.”

No new shared design-system component is introduced. Use the existing vendored
COFAIR cards, tables, selects, badges/callouts, typography, spacing, and color
tokens.

## Error and empty states

- No complete cost run yet: cards show em dashes and explain that the first
  fully instrumented run is pending.
- Latest attempted run incomplete: show an explicit incomplete status and keep
  it out of comparisons; the date selector may still expose it for diagnosis.
- Missing comparison history: current amount remains visible and comparison
  reads `Comparison unavailable`.
- Missing model price: mark the event incomplete; never substitute another
  model’s rate or zero.
- A model or corpus change remains segmented in trends and separately labeled;
  it does not reset raw cost history, but the September 3 display epoch controls
  what users can see.

## Verification

Python tests:

- canonical A–F pack and purpose-specific task sets;
- E executes three relational turns and aggregates usage correctly;
- E turn events retain prior assistant context and do not double-count on replay;
- every runner path emits the expected cost-event shape;
- model fallthrough freezes the served model’s price;
- incomplete/unknown cost events withhold a daily total;
- provider × tier/task/source rollups reconcile exactly to daily totals;
- current-run, MTD, and YTD calendar comparisons, including month-end,
  leap-year, zero-baseline, and missing-history cases;
- shared September 3 epoch filters both dashboards.

Browser checks:

- Full suite and Task Corpus both show A–F; every individual option selects the
  matching row;
- E draws Input, Output, and Total after the baseline run;
- Content density, internal USD, and the quoted OpenAI warning are absent;
- measure descriptions render below the chart;
- three tabs support click and arrow-key navigation;
- all cost cards, unavailable states, date selection, and nested expansion
  render at desktop and narrow widths with no console errors;
- provider/tier/task sums reconcile with the published artifact;
- design-system sync check remains green.

After implementation, manually dispatch the affected daily workflows, rebuild,
push generated artifacts, and verify the September 3 baseline at
`https://cofair.org/tokens/`.
