# Token Costs and Corpus Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Normalize `/tokens` on one A–F generating corpus, restart both dashboards on 2026-09-03, and add an auditable Costs tab covering meter and ledger requests.

**Architecture:** `task_corpus.py` becomes explicit about canonical, generating, and ledger task sets. Meter and ledger runners write deterministic request-level rows to a new append-only `cost_events.json`; the dashboard builder validates complete daily coverage and publishes cost comparisons/tree rollups in `equivalence.json`. The zero-build dashboard consumes that derived block and uses existing COFAIR cards, tabs, tables, and tokens.

**Tech Stack:** Python 3.11 (`unittest`, JSON artifacts, requests), GitHub Actions YAML, zero-build HTML/CSS/ES modules, Chart.js, vendored `@cofair/ui`.

## Global Constraints

- Canonical visible corpus is exactly A–F; E is never labeled “count-only.”
- E is a three-turn generated conversation and its three requests aggregate into one task row.
- `DASHBOARD_START_DATE = "2026-09-03"` applies to both pricing and tokens; raw history remains append-only.
- Public trend measures are Total, Output, Input, and Fixed request overhead only.
- The active trend description renders below the chart.
- Costs are labeled **Estimated spend**, never invoice cost.
- A cost total is published only when every expected meter and ledger request is complete.
- No new `@cofair/ui` component; use existing vendored classes and `--cofair-*` tokens only.
- No provider key, token, or unredacted provider error enters an artifact.

---

### Task 1: Canonical A–F corpus and generated task E

**Files:**
- Modify: `scripts/task_corpus.py`
- Modify: `scripts/test_task_corpus.py`
- Modify: `scripts/test_run_tokenizer_ledger.py`

**Interfaces:**
- Produces: `TASK_IDS`, `GENERATING_TASK_IDS`, `LEDGER_TASK_IDS`
- Produces: `E_USER_PROMPTS: tuple[str, str, str]`
- Preserves: `METER_TASK_IDS` as a compatibility alias to `GENERATING_TASK_IDS`
- Produces: `TASK_PACKS["suiteLong"] == list(TASK_IDS)`

- [ ] **Step 1: Write failing corpus tests**

Add tests equivalent to:

```python
def test_the_full_suite_is_canonical_a_through_f(self) -> None:
    self.assertEqual(TASK_IDS, ("A", "B", "C", "D", "E", "F"))
    self.assertEqual(GENERATING_TASK_IDS, TASK_IDS)
    self.assertEqual(LEDGER_TASK_IDS, ("A", "B", "C", "D", "F"))
    self.assertEqual(TASK_PACKS["suiteLong"], list(TASK_IDS))

def test_task_e_is_three_frozen_relational_prompts(self) -> None:
    self.assertEqual(len(E_USER_PROMPTS), 3)
    self.assertIn("flagship model", E_USER_PROMPTS[0])
    self.assertIn("Challenge your recommendation", E_USER_PROMPTS[1])
    self.assertIn("three-step policy", E_USER_PROMPTS[2])
    self.assertIn("E", TASK_PROMPTS)
    self.assertEqual(TASK_SPECS["E"]["label"], "Chat conversation")

def test_the_daily_ledger_excludes_conversation_task_e(self) -> None:
    self.assertEqual(TASK_SETS["all"], list(LEDGER_TASK_IDS))
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python3 -m unittest scripts.test_task_corpus scripts.test_run_tokenizer_ledger -v
```

Expected: failures for missing constants/prompts and old task-set membership.

- [ ] **Step 3: Implement explicit task sets and E**

In `task_corpus.py`, define:

```python
TASK_IDS = ("A", "B", "C", "D", "E", "F")
GENERATING_TASK_IDS = TASK_IDS
LEDGER_TASK_IDS = ("A", "B", "C", "D", "F")
METER_TASK_IDS = GENERATING_TASK_IDS

E_USER_PROMPTS = (
    "An organization uses AI for both routine administrative work and "
    "high-stakes analysis. Explain how it should decide when to use a flagship "
    "model and when to use a workhorse model.",
    "Challenge your recommendation from the perspective of a team that values "
    "reliability more than cost.",
    "Now revise the recommendation to address that challenge while keeping "
    "spending predictable. End with a practical three-step policy.",
)
```

Represent `TASK_PROMPTS["E"]` as the newline-joined frozen user prompts for
metadata/character accounting, add a normal `TASK_SPECS["E"]`, set
`TASK_PACKS["suiteLong"] = list(TASK_IDS)`, and bump `CORPUS_VERSION` and
`CHAT_CORPUS_VERSION` with comments describing the break.

Change `run_tokenizer_ledger.TASK_SETS["all"]` to `list(LEDGER_TASK_IDS)`.
Keep the old `CHAT_TRANSCRIPT` bytes available only for interpreting historical
wrapper rows; do not schedule or publish it as the current task.

- [ ] **Step 4: Run focused tests**

Run the Step 2 command.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/task_corpus.py scripts/test_task_corpus.py scripts/test_run_tokenizer_ledger.py
git commit -m "PD-45: normalize the corpus and define generated task E"
```

---

### Task 2: Request-level cost-event core

**Files:**
- Create: `scripts/cost_events.py`
- Create: `scripts/test_cost_events.py`
- Modify: `dashboard/data/.gitignore` only if generated JSON patterns require it

**Interfaces:**
- Produces: `CostEventInput` dataclass
- Produces: `build_cost_event(...) -> dict`
- Produces: `merge_cost_events(existing: list[dict], incoming: list[dict]) -> list[dict]`
- Produces: `load_cost_events(path: Path = COST_EVENTS_FILE) -> list[dict]`
- Produces: `save_cost_events(rows: list[dict], path: Path = COST_EVENTS_FILE) -> None`

- [ ] **Step 1: Write failing cost-event tests**

Cover deterministic identity, exact arithmetic, incomplete prices, zero-cost
native count endpoints, replay replacement, and no secret/error fields:

```python
def test_generation_cost_uses_unrounded_usage(self) -> None:
    event = build_cost_event(
        date="2026-09-03", source="meter", provider_id="openai",
        tier="flagship", task_id="A", turn=None, request_kind="generation",
        api_model="chat-latest", input_tokens=34, output_tokens=163,
        input_price_per_1m=1.25, output_price_per_1m=10.0,
        pricing_snapshot_date="2026-09-03", corpus_version="3.0.0",
        chat_corpus_version="2.0.0", run_id="2026-09-03:two:1",
    )
    self.assertAlmostEqual(event["input_cost_usd"], 34 / 1_000_000 * 1.25)
    self.assertAlmostEqual(event["output_cost_usd"], 163 / 1_000_000 * 10.0)
    self.assertTrue(event["complete"])

def test_replay_replaces_same_logical_request(self) -> None:
    merged = merge_cost_events([old_event], [new_event])
    self.assertEqual(len(merged), 1)
    self.assertEqual(merged[0]["estimated_cost_usd"], new_event["estimated_cost_usd"])
```

- [ ] **Step 2: Run test and verify failure**

```bash
.venv/bin/python3 -m unittest scripts.test_cost_events -v
```

Expected: import failure for `cost_events`.

- [ ] **Step 3: Implement the module**

Use an immutable input dataclass and a deterministic key:

```python
def cost_event_id(*, date, source, provider_id, tier, task_id, turn,
                  request_kind, run_id, replicate=1) -> str:
    return ":".join(map(str, (
        date, source, provider_id, tier, task_id, turn or 0,
        request_kind, run_id, replicate,
    )))

def build_cost_event(..., billable: bool = True) -> dict:
    complete = (
        input_tokens is not None
        and output_tokens is not None
        and input_price_per_1m is not None
        and output_price_per_1m is not None
    )
    if not billable:
        input_cost = output_cost = total = 0.0
        complete = True
    elif complete:
        input_cost = input_tokens / 1_000_000 * input_price_per_1m
        output_cost = output_tokens / 1_000_000 * output_price_per_1m
        total = input_cost + output_cost
    else:
        input_cost = output_cost = total = None
    return {...}
```

`save_cost_events` writes:

```json
{"generated_at": "...", "row_count": 1, "rows": []}
```

Sort by date/source/provider/tier/task/turn/event id. Do not accept arbitrary
error text or credentials in the event interface.

- [ ] **Step 4: Run tests**

Run the Step 2 command.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/cost_events.py scripts/test_cost_events.py
git commit -m "PD-45: add deterministic request-level cost events"
```

---

### Task 3: Multi-turn E and meter cost events

**Files:**
- Modify: `scripts/run_equivalence_tasks.py`
- Modify: `scripts/test_run_equivalence_tasks.py`

**Interfaces:**
- Extends: `Usage` with `assistant_text: str`
- Produces: `_run_messages(provider_id, api_model, messages, max_tokens, api_key) -> Usage`
- Produces: `run_provider_conversation(entry, max_tokens, dry_run) -> tuple[TaskResult, list[TurnResult]]`
- Consumes: `build_cost_event`, `merge_cost_events`, `save_cost_events`

- [ ] **Step 1: Write failing provider-shape tests**

Test all four API shapes with mocked responses:

```python
def test_openai_conversation_keeps_prior_assistant_output(self) -> None:
    responses = [fake_openai("first", 10, 20), fake_openai("second", 30, 40),
                 fake_openai("third", 50, 60)]
    with mock.patch.object(runner, "_http_request", side_effect=responses) as request:
        result, turns = runner.run_provider_conversation(OPENAI_ENTRY, None, False)
    second_messages = request.call_args_list[1].kwargs["json"]["messages"]
    self.assertEqual(second_messages[1], {"role": "assistant", "content": "first"})
    self.assertEqual((result.tokens_in, result.tokens_out), (90, 120))
    self.assertEqual([turn.turn for turn in turns], [1, 2, 3])

def test_dry_run_writes_neither_runs_nor_cost_events(self) -> None:
    ...
```

Add equivalent body assertions for Anthropic Messages, Gemini
`user`/`model` contents, and Bedrock Converse content arrays. Assert model
fallback prices remain attached to every turn.

- [ ] **Step 2: Run focused tests and verify failure**

```bash
.venv/bin/python3 -m unittest scripts.test_run_equivalence_tasks -v
```

Expected: missing conversation interfaces and absent assistant text.

- [ ] **Step 3: Generalize provider generation to message arrays**

Make the text helpers delegate to message helpers. Parse assistant text:

```python
class Usage(NamedTuple):
    tokens_in: int
    tokens_out: int
    truncated: bool
    cap_sent: int | None
    assistant_text: str = ""
```

Provider extraction:

```python
# Anthropic
assistant_text = "".join(
    block.get("text", "") for block in payload.get("content", [])
    if block.get("type") == "text"
)
# OpenAI compatible
assistant_text = choices[0].get("message", {}).get("content", "")
# Gemini
assistant_text = "".join(
    part.get("text", "") for part in candidates[0].get("content", {}).get("parts", [])
)
# Bedrock
assistant_text = "".join(
    block.get("text", "") for block in payload.get("output", {})
    .get("message", {}).get("content", [])
)
```

Preserve Anthropic’s cap discovery/step-up loop by changing `_anthropic_call`
to accept normalized messages rather than creating one user message internally.

- [ ] **Step 4: Implement conversation aggregation and event emission**

For E:

```python
messages = []
turns = []
for turn, prompt in enumerate(E_USER_PROMPTS, 1):
    messages.append({"role": "user", "content": prompt})
    usage = _run_messages(...)
    turns.append(TurnResult(turn, usage, list(messages)))
    messages.append({"role": "assistant", "content": usage.assistant_text})
```

The E meter row sums all three usages and stamps `chat_corpus_version`.
Single-turn A–D/F rows remain one event. Build one `source="meter"` cost event
per successful request, with deterministic `run_id` including date/mode/
replicate. Persist runs and events only when `not args.dry_run`.

- [ ] **Step 5: Run focused and full Python tests**

```bash
.venv/bin/python3 -m unittest scripts.test_run_equivalence_tasks scripts.test_cost_events -v
.venv/bin/python3 -m unittest discover -s scripts -p 'test_*.py'
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_equivalence_tasks.py scripts/test_run_equivalence_tasks.py
git commit -m "PD-45: generate task E and meter every request cost"
```

---

### Task 4: Ledger cost events and wrapper retirement

**Files:**
- Modify: `scripts/provider_token_count.py`
- Modify: `scripts/run_tokenizer_ledger.py`
- Modify: `scripts/test_provider_token_count.py`
- Modify: `scripts/test_run_tokenizer_ledger.py`
- Modify: `.github/workflows/daily-token-equivalence.yml`
- Modify: `scripts/ops/verify_token_runs.py`
- Modify: `scripts/ops/runbooks.py`

**Interfaces:**
- Produces: `CountUsage(tokens_in, tokens_out, request_kind, billable)`
- Changes: `count_prompt_tokens_text(...) -> tuple[str, CountUsage | None, str | None, str]`
- Emits: `source="ledger"` cost events for A/B/C/D/F

- [ ] **Step 1: Write failing count-usage tests**

Assert:

```python
def test_openai_completion_probe_retains_output_usage(self) -> None:
    usage, model = _count_one("openai", "chat-latest", "hello", "key", False)
    self.assertEqual(usage.request_kind, "completion_probe")
    self.assertEqual(usage.tokens_out, 16)
    self.assertTrue(usage.billable)

def test_gemini_native_count_is_non_billable(self) -> None:
    usage, model = _count_one("google", "gemini-pro-latest", "hello", "key", False)
    self.assertEqual(usage.request_kind, "count_endpoint")
    self.assertEqual(usage.tokens_out, 0)
    self.assertFalse(usage.billable)
```

Mock fallback from Gemini `countTokens` to `generateContent` and assert it
becomes a billable completion probe with observed output usage.

- [ ] **Step 2: Run tests and verify failure**

```bash
.venv/bin/python3 -m unittest scripts.test_provider_token_count scripts.test_run_tokenizer_ledger -v
```

Expected: current integer-only count interface fails assertions.

- [ ] **Step 3: Return structured usage from count adapters**

Implement:

```python
class CountUsage(NamedTuple):
    tokens_in: int
    tokens_out: int
    request_kind: str
    billable: bool
```

Parse output usage for Anthropic/OpenAI-compatible/Bedrock and Gemini fallback.
Use `request_kind="count_endpoint", billable=False` only for a successful
provider-native count endpoint. Keep public count helper status/error/model
behavior otherwise unchanged.

- [ ] **Step 4: Emit ledger cost events**

Resolve the served model’s prices from the priced `api_candidates` row, not the
requested alias. Add a focused helper in `run_tokenizer_ledger.py`:

```python
def prices_for_api_model(model: dict, api_model: str) -> tuple[float | None, float | None]:
    for candidate in [model, *(model.get("api_candidates") or [])]:
        if api_model in api_model_candidates(...candidate...):
            return candidate.get("input_price"), candidate.get("output_price")
    return None, None
```

Write one event per successful ledger row. A native count endpoint gets zero
cost; a completion probe uses observed input/output usage and served-model
prices. Preserve the existing dry-run no-write guarantee for both ledger and
cost events.

- [ ] **Step 5: Retire wrapper scheduling and health requirements**

Remove the wrapper runner step and `--sources wrapper` verification from
`daily-token-equivalence.yml`. Remove wrapper from default active `SOURCES` and
retry runbooks, while keeping `run_wrapper_overhead.py` and historical data
readable. Update workflow assertions in tests to require generation A–F and
ledger `--tasks all`.

- [ ] **Step 6: Run tests and parse workflow YAML**

```bash
.venv/bin/python3 -m unittest scripts.test_provider_token_count scripts.test_run_tokenizer_ledger scripts.test_verify_token_runs -v
.venv/bin/python3 - <<'PY'
import yaml
for path in (".github/workflows/daily-token-equivalence.yml",
             ".github/workflows/daily-tokenizer-ledger.yml"):
    yaml.safe_load(open(path))
print("workflow yaml ok")
PY
```

Expected: PASS and `workflow yaml ok`.

- [ ] **Step 7: Commit**

```bash
git add scripts/provider_token_count.py scripts/run_tokenizer_ledger.py \
  scripts/test_provider_token_count.py scripts/test_run_tokenizer_ledger.py \
  scripts/ops/verify_token_runs.py scripts/ops/runbooks.py \
  .github/workflows/daily-token-equivalence.yml
git commit -m "PD-45: price ledger probes and retire wrapper collection"
```

---

### Task 5: Cost completeness, rollups, comparisons, and epoch

**Files:**
- Modify: `scripts/build_dashboard_data.py`
- Modify: `scripts/test_build_dashboard_data.py`

**Interfaces:**
- Produces: `build_costs(events, selected_models, latest_date) -> dict`
- Produces: `build_cost_comparison(current, previous) -> dict`
- Publishes: `equivalence.json["costs"]`
- Changes: `DASHBOARD_START_DATE = "2026-09-03"`

- [ ] **Step 1: Write failing builder tests**

Add fixtures covering one complete date of 14 panel rows with:

- meter A/B/C/D/F once;
- meter E turns 1/2/3;
- ledger A/B/C/D/F;
- input/output/supporting totals.

Assertions:

```python
def test_complete_day_reconciles_cost_tree(self) -> None:
    costs = build_costs(self.complete_events("2026-09-03"), PANEL)
    day = costs["daily"][0]
    self.assertTrue(day["complete"])
    leaves = [
        detail["estimated_cost_usd"]
        for row in day["provider_tiers"]
        for task in row["tasks"]
        for detail in task["details"]
    ]
    self.assertAlmostEqual(sum(leaves), day["estimated_spend_usd"])

def test_one_missing_e_turn_withholds_the_day(self) -> None:
    events = [e for e in self.complete_events("2026-09-03")
              if not (e["task_id"] == "E" and e["turn"] == 2)]
    self.assertFalse(build_costs(events, PANEL)["daily"][0]["complete"])
```

Also test current-run comparison, prior-month same-day clamp, leap year,
zero baseline, absent prior period, and missing scheduled day.

- [ ] **Step 2: Run tests and verify failure**

```bash
.venv/bin/python3 -m unittest scripts.test_build_dashboard_data -v
```

Expected: missing `build_costs` and old epoch assertions.

- [ ] **Step 3: Implement daily rollup and completeness**

Load `cost_events.json`. Expected identities per provider/tier are:

```python
EXPECTED_METER = {("A", None), ("B", None), ("C", None),
                  ("D", None), ("E", 1), ("E", 2), ("E", 3),
                  ("F", None)}
EXPECTED_LEDGER = {("A", None), ("B", None), ("C", None),
                   ("D", None), ("F", None)}
```

Group leaves by date → provider/tier → task → details. Split:

```python
input_cost_usd = sum(detail["input_cost_usd"] or 0 for generation details)
output_cost_usd = sum(detail["output_cost_usd"] or 0 for generation details)
supporting_cost_usd = sum(detail["estimated_cost_usd"] or 0
                          for ledger details)
```

Mark a date complete only when every selected panel row has exactly the
expected complete event keys.

- [ ] **Step 4: Implement calendar comparisons**

Use `calendar.monthrange` and date ranges. Publish:

```python
{
  "complete_start_date": "2026-09-03",
  "latest_attempted_date": "...",
  "latest_complete_date": "...",
  "comparisons": {
    "current_run": {"amount_usd": ..., "delta_usd": ..., "delta_pct": ..., "status": ...},
    "current_month": {...},
    "year_to_date": {...},
  },
  "daily": [...]
}
```

Require every scheduled date in both calendar windows to be complete. Use
`status="comparison_unavailable"` or `"new_baseline"` instead of dividing by
missing/zero values.

- [ ] **Step 5: Set epoch and update published metadata**

Change:

```python
DASHBOARD_START_DATE = "2026-09-03"
```

Publish `task_ids`, `generating_task_ids`, `ledger_task_ids`, current E
metadata, historical wrapper status, and the `costs` block. Remove content
density from UI-facing notes but preserve raw fits if still needed for fixed
overhead.

- [ ] **Step 6: Run tests and rebuild**

```bash
.venv/bin/python3 -m unittest scripts.test_build_dashboard_data -v
.venv/bin/python3 scripts/build_dashboard_data.py --rebuild
```

Expected: PASS; both `index.json` and `equivalence.json` publish
`dashboard_start_date: "2026-09-03"`; costs block is present, pending until the
first fully instrumented run.

- [ ] **Step 7: Commit**

```bash
git add scripts/build_dashboard_data.py scripts/test_build_dashboard_data.py \
  dashboard/data/index.json dashboard/data/equivalence.json
git commit -m "PD-45: publish complete daily cost rollups and reset the epoch"
```

---

### Task 6: Normalize trends and build the Costs tab

**Files:**
- Modify: `dashboard/tokens/index.html`
- Modify: `dashboard/tokens/app.js`
- Modify: `dashboard/tokens/styles.css`
- Modify: `dashboard/labels.js`
- Modify: `dashboard/labels.test.js`

**Interfaces:**
- Consumes: `state.eq.costs`
- Produces: `formatEstimatedSpend(usd) -> string`
- Produces: `formatCostDelta(comparison) -> string`
- Produces: `renderCosts()`

- [ ] **Step 1: Add failing formatter tests**

In `labels.test.js`:

```javascript
test("formatEstimatedSpend adapts precision", () => {
  assert.equal(formatEstimatedSpend(2.345), "$2.35");
  assert.equal(formatEstimatedSpend(0.23456), "$0.2346");
  assert.equal(formatEstimatedSpend(0.00123456), "$0.001235");
});

test("formatCostDelta never invents a comparison", () => {
  assert.equal(formatCostDelta({ status: "comparison_unavailable" }),
               "Comparison unavailable");
  assert.equal(formatCostDelta({ status: "ok", delta_pct: 8.2,
                                 delta_usd: 0.0142 }),
               "+8.2% · +$0.0142");
});
```

- [ ] **Step 2: Run JS tests and verify failure**

```bash
node --test dashboard/labels.test.js
```

Expected: missing formatter exports.

- [ ] **Step 3: Implement formatters and trends cleanup**

Export the formatters from `labels.js`. In `index.html`:

- set Full suite to A–F and add E in order;
- leave only total/output/input/fixed-overhead measures;
- move `#metricNote` after `.chart-card`;
- remove `#healthNote`;
- add the third Costs tab and panel.

In `app.js`:

- remove `ledger_content_density`, `usd`, and `gateInternalMeasures`;
- make `packTasks()` read `packTaskIds()` rather than a subset;
- remove special `chatTaskRow`, wrapper ledger fusion, and chat-only drawer;
- render E through the normal task path using its three prompts;
- keep health chip/title state but delete visible warning construction;
- preserve fixed-overhead fitting and chart rendering.

- [ ] **Step 4: Add Costs markup and renderer**

Use three cards:

```html
<div class="cost-cards">
  <article class="cofair-card cost-card" data-cost-period="current_run">...</article>
  <article class="cofair-card cost-card" data-cost-period="current_month">...</article>
  <article class="cofair-card cost-card" data-cost-period="year_to_date">...</article>
</div>
```

Render provider/tier parent rows with:

```html
<button class="cost-expand" aria-expanded="false"
        data-cost-row="openai|flagship">...</button>
```

Task/detail rows remain in the DOM only while expanded. Escape all artifact
strings through the existing `esc()` helper. A row’s displayed columns must
come directly from builder totals, not be recomputed from formatted strings.

- [ ] **Step 5: Add responsive token-only CSS**

Use:

```css
.cost-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--cofair-space-4);
}
.metric-note { margin: var(--cofair-space-2) 0 0; }
.cost-row--task .cost-label { padding-left: var(--cofair-space-6); }
.cost-row--detail .cost-label { padding-left: var(--cofair-space-9); }
@media (max-width: 40rem) {
  .cost-cards { grid-template-columns: 1fr; }
}
```

Use semantic tokens only. Comparison states include text (`Increase`,
`Decrease`, or unavailable), not color alone.

- [ ] **Step 6: Run syntax and unit checks**

```bash
node --test dashboard/labels.test.js
node --check dashboard/tokens/app.js
python3 scripts/sync_design_system.py --check
```

Expected: all PASS.

- [ ] **Step 7: Browser verification**

Serve `dashboard/`, then verify:

- all three tabs by click and left/right arrows;
- Full suite shows A–F in selector and corpus;
- individual E draws after a fixture/baseline;
- only four measures exist;
- note is below chart;
- quoted OpenAI warning is absent;
- cards wrap at mobile width;
- run-date selection and nested rows work;
- no console errors.

- [ ] **Step 8: Commit**

```bash
git add dashboard/tokens/index.html dashboard/tokens/app.js \
  dashboard/tokens/styles.css dashboard/labels.js dashboard/labels.test.js
git commit -m "PD-45: normalize A-F and add the Costs tab"
```

---

### Task 7: Full verification, PM write-back, baseline run, and live check

**Files:**
- Modify: `../cofair/docs/pm/TASKS.md`
- Modify: `../cofair/docs/pm/TECHNICAL_DECISIONS.md`
- Modify: generated dashboard data after workflow baseline

**Interfaces:**
- Records: PD-45 / next D-number
- Produces: first complete 2026-09-03 cost baseline

- [ ] **Step 1: Run complete local verification**

```bash
.venv/bin/python3 -m unittest discover -s scripts -p 'test_*.py'
node --test dashboard/labels.test.js
node --check dashboard/app.js
node --check dashboard/tokens/app.js
python3 scripts/sync_design_system.py --check
```

Expected: all tests pass, syntax checks exit 0, vendored files are current.

- [ ] **Step 2: Inspect artifact invariants**

Run a focused Python assertion script confirming:

```python
assert eq["dashboard_start_date"] == "2026-09-03"
assert eq["task_packs"]["suiteLong"] == ["A", "B", "C", "D", "E", "F"]
assert eq["generating_task_ids"] == ["A", "B", "C", "D", "E", "F"]
assert eq["ledger_task_ids"] == ["A", "B", "C", "D", "F"]
assert "costs" in eq
```

- [ ] **Step 3: Write PM state**

Add PD-45 to `../cofair/docs/pm/TASKS.md`. Add a dated decision covering:

- E’s generated three-turn semantics and version break;
- A–F normalization;
- request-level estimated-spend events and completeness;
- September 3 shared epoch;
- four trend measures and note placement;
- Costs comparison semantics and invoice disclaimer.

- [ ] **Step 4: Commit and push both repos**

```bash
git add -A
git commit -m "PD-45: complete token costs and corpus normalization"
git pull --rebase
git push

cd ../cofair
git add docs/pm/TASKS.md docs/pm/TECHNICAL_DECISIONS.md
git commit -m "PD-45: record token costs and corpus normalization"
git pull --rebase
git push
```

- [ ] **Step 5: Dispatch complete baseline workflows**

After the code is on `main`, dispatch the daily ledger and daily equivalence
workflows for `2026-09-03`. Wait for both to finish; if one fails, inspect the
failed step rather than publishing a partial total.

- [ ] **Step 6: Pull generated rows, rebuild if required, and verify completeness**

Confirm the latest cost date has:

- 14 provider/tier rows;
- 6 meter tasks per row, with E turns 1–3;
- 5 ledger tasks per row;
- `complete: true`;
- parent/task/detail totals reconciling exactly.

- [ ] **Step 7: Verify live**

At `https://cofair.org/tokens/`, confirm:

- all three tabs and A–F labels are live;
- four measures only;
- Costs shows the Current Run amount and unavailable month/year comparisons;
- no old health sentence;
- no console/network errors.

- [ ] **Step 8: Run UI conformance gate if shared UI changed**

No shared UI change is expected. If implementation touched `@cofair/ui` or
`designs/**`, run:

```bash
cd ../cofair
node scripts/conformance-gate.mjs
```

Expected: `GATE: PASSED`.
