#!/usr/bin/env python3
"""Build dashboard artifacts from pricing_history/.

Modes:
  --rebuild  walk every pricing_history/*.json (handles 1.x and 2.x schemas),
             regenerate series.json + models.json + index.json from scratch.
  --append   read pricing.json (assumed current schema 2.x) and the date is
             today UTC by default. Appends today's rows to series.json
             (replacing any existing rows for that date), refreshes
             models.json + index.json. O(today) work, suitable for CI.

Artifacts:
  pricing_history/series.json   long-form per-day per-pricing-row token prices
  pricing_history/models.json   per-pricing-row lifecycle (first/last seen, deprecated_on)
  pricing_history/index.json    list of snapshot dates + schema versions

Filter rules: we only keep rows priced per_1M_tokens with service_tier in
{null, "standard"} so the chart compares apples to apples across providers.
Image/video/transcription rows are skipped (different units). Google Vertex
often splits modalities into separate pricing_ids — text output can be
`output_unit=per_1M_tokens` with a null `input_unit`; those must be kept.
The historical pre-2026-06-17 snapshots are not granular enough to trust for
chart trend analysis (added in a one-time archive import), so dashboard
artifacts intentionally begin at 2026-06-17.
The 2.x `pricing_id` is used as the line key; for 1.x we synthesize one.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sys
from collections import Counter
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "ops"))
from cost_events import load_cost_events  # noqa: E402
from provider_faults import remedy_for_error  # noqa: E402
from task_corpus import (  # noqa: E402
    CHAT_CORPUS_VERSION,
    CHAT_TASK,
    CHAT_TRANSCRIPT,
    CORPUS_VERSION,
    DEGENERATE_TASK_IDS,
    E_USER_PROMPTS,
    GENERATING_TASK_IDS,
    is_degenerate,
    LEDGER_TASK_IDS,
    METER_TASK_IDS,
    OUTPUT_CEILING,
    OUTPUT_POLICY_VERSION,
    TASK_DEFINITIONS,
    TASK_IDS,
    TASK_PACKS,
    TASK_PROMPTS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SHARED_ENV = (REPO_ROOT.parent / "cofair" / ".env" / ".env.cofair")
load_dotenv()
if DEFAULT_SHARED_ENV.exists():
    load_dotenv(DEFAULT_SHARED_ENV, override=False)
HISTORY_DIR = REPO_ROOT / "pricing_history"
LIVE_FILE = REPO_ROOT / "pricing.json"

DASHBOARD_DATA_DIR = REPO_ROOT / "dashboard" / "data"
SERIES_FILE = DASHBOARD_DATA_DIR / "series.json"
MODELS_FILE = DASHBOARD_DATA_DIR / "models.json"
INDEX_FILE = DASHBOARD_DATA_DIR / "index.json"
EQUIVALENCE_FILE = DASHBOARD_DATA_DIR / "equivalence.json"
EQUIVALENCE_RUNS_FILE = DASHBOARD_DATA_DIR / "equivalence_runs.json"
TOKENIZER_LEDGER_FILE = DASHBOARD_DATA_DIR / "tokenizer_ledger.json"
WRAPPER_RUNS_FILE = DASHBOARD_DATA_DIR / "wrapper_runs.json"

TOKEN_UNIT = "per_1M_tokens"

# The epoch the published dashboards start from. Everything before it was
# collected under earlier practices — a weekly meter, a Monday-anchored run
# date, a partial task set — so mixing it with what we collect now would read as
# drift in the instrument rather than a change in how the instrument was run.
# Raw pricing_history snapshots and the run files keep their full history; this
# only governs what the dashboards are built from, so moving the date back
# restores the older record.
#
# Moved to the first run under output policy 4.0.0 (no output cap, truncation read
# from the provider). Strictly, only the meter's `tokens_out` and the totals built
# on it lose comparability at that boundary: pricing is an independent list-price
# scrape, and the tokenizer ledger counts with an explicit 1-token cap so it never
# generates. Both were reset anyway, by decision, so the published record has one
# start line instead of three — the alternative was a page whose series began on
# different days for reasons no reader could see. The cost is a dashboard that is
# empty until the first runs land, which is why this date is not moved casually.
#
# Moved again to 2026-09-03 (PD-45), the first run on the canonical A–F corpus.
# Task D's prompt was replaced and task E became a generated three-turn
# conversation rather than a counted transcript, so a row's task id no longer
# means the same thing across that line; and estimated spend is only defined from
# the day every meter and ledger request started emitting a priced cost event.
# Pricing and tokens share the date so the two dashboards start together.
DASHBOARD_START_DATE = "2026-09-03"


# ---- normalization ---------------------------------------------------------

def _synth_pricing_id_v1(row: dict) -> str:
    return f"{row['provider_id']}-{row['model_id']}-{row.get('type','chat')}"


def _is_token_priced_v2(row: dict) -> bool:
    """True when any token price field uses per_1M_tokens (input and/or output)."""
    return any(
        row.get(key) == TOKEN_UNIT
        for key in ("input_unit", "output_unit", "cached_input_unit", "cache_read_unit")
    )


def _is_amazon_owned_row(row: dict) -> bool:
    """Keep only Amazon-built models under provider_id 'aws'.

    Historical snapshots captured Bedrock's resold third-party models (Claude,
    Cohere, Llama, …) under provider_id 'aws'. Those are immutable on disk, but
    they must not surface in the Amazon index. Ownership is asserted from the
    Nova/Titan families only.
    """
    haystack = f"{row.get('model_id', '')} {row.get('display_name', '')}".lower()
    return "nova" in haystack or "titan" in haystack


def _is_deepseek_owned_row(row: dict) -> bool:
    """Keep only DeepSeek-built models under provider_id 'deepseek'."""
    haystack = f"{row.get('model_id', '')} {row.get('display_name', '')}".lower()
    return haystack.strip().startswith("deepseek-") or " deepseek-" in f" {haystack}"


def normalize_snapshot(snapshot: dict, date: str) -> list[dict]:
    """Flatten one pricing.json snapshot into chart-ready rows for `date`."""
    schema = snapshot.get("meta", {}).get("schema_version", "")
    rows: list[dict] = []

    for r in snapshot.get("pricing", []):
        # Hide historical resold third-party rows without rewriting immutable
        # snapshots on disk (Amazon = Nova/Titan; DeepSeek = deepseek-* only).
        if r.get("provider_id") == "aws" and not _is_amazon_owned_row(r):
            continue
        if r.get("provider_id") == "deepseek" and not _is_deepseek_owned_row(r):
            continue
        if schema.startswith("1."):
            # 1.x: only keep chat rows priced per_1M_tokens
            if r.get("type") != "chat":
                continue
            if r.get("unit") != TOKEN_UNIT:
                continue
            rows.append({
                "date": date,
                "pricing_id": _synth_pricing_id_v1(r),
                "provider_id": r["provider_id"],
                "model_id": r["model_id"],
                "display_name": r.get("display_name") or r["model_id"],
                "service_tier": "standard",
                "category": "standard_api",
                "modality": r.get("modality"),
                "context_window": r.get("context_window"),
                "input_price": r.get("input_price"),
                "output_price": r.get("output_price"),
                "cached_input_price": None,
                "currency": r.get("currency", "USD"),
                "is_active": r.get("is_active", True),
            })
        else:
            # 2.x — keep input-only, output-only, or dual token rows
            if not _is_token_priced_v2(r):
                continue
            tier = r.get("service_tier")
            if tier not in (None, "standard"):
                continue
            rows.append({
                "date": date,
                "pricing_id": r["pricing_id"],
                "provider_id": r["provider_id"],
                "model_id": r["model_id"],
                "display_name": r.get("display_name") or r["model_id"],
                "service_tier": tier or "standard",
                "category": r.get("category"),
                "modality": r.get("modality"),
                "context_window": r.get("context_window"),
                "input_price": r.get("input_price"),
                "output_price": r.get("output_price"),
                "cached_input_price": r.get("cache_read_price"),
                "currency": r.get("currency", "USD"),
                "is_active": r.get("is_active", True),
            })
    return rows


def include_dashboard_date(date: str) -> bool:
    return bool(date) and date >= DASHBOARD_START_DATE


def run_date_of(row: dict) -> str:
    """The day a meter or wrapper row belongs to.

    Reads `run_week` as a fallback so rows written before the daily cadence are
    dated by their real anchor and excluded on their own merits, rather than
    landing dateless and being dropped for the wrong reason.
    """
    return row.get("run_date") or row.get("run_week") or ""


# ---- aggregation -----------------------------------------------------------

DEPRECATED_HINTS = ("deprecated", "retired", "legacy")

# Two tiers only: flagship and workhorse. The middle "default" tier was dropped
# because it mostly tracked the flagship and doubled inference spend without
# adding a distinct signal.
TIER_ORDER = ("flagship", "workhorse")

# The index runs on one fixed daily cadence so every task is directly comparable
# day over day, and the annual budget is a run's cost times the days in a year.
DAILY_RUNS_PER_YEAR = 365

# Identifiability floor for the daily overhead/content split (build_ledger_fits).
# Two parameters need at least three tasks, and they need those tasks to differ in
# length by an order of magnitude — otherwise the constant and the slope trade off
# against each other and the fitted rate wanders with the task mix.
MIN_FIT_TASKS = 3
MIN_FIT_CHAR_SPAN = 10.0

RUNS_PER_YEAR_BY_CADENCE = {
    "daily": DAILY_RUNS_PER_YEAR,
    "weekly": 52,
    "biweekly": 26,
    "monthly": 12,
}

TIER_CANDIDATES = {
    "anthropic": {
        "flagship": ["claude-opus-5", "claude-opus-4.8"],
        "workhorse": ["claude-haiku-4.5"],
    },
    "openai": {
        "flagship": ["chat-latest", "gpt-5.6-sol"],
        "workhorse": ["gpt-5.6-luna"],
    },
    "google": {
        "flagship": ["gemini-3.1-pro", "gemini-2.5-pro"],
        "workhorse": ["gemini-3-flash", "gemini-2.0-flash"],
    },
    "xai": {
        "flagship": ["grok-4.6", "grok-4.5"],
        "workhorse": ["grok-build-0.1"],
    },
    "aws": {
        "flagship": ["nova-premier", "nova-2.0-pro", "nova-pro"],
        "workhorse": ["nova-micro", "nova-2.0-lite", "nova-lite"],
    },
    "deepseek": {
        "flagship": ["deepseek-v4-pro"],
        "workhorse": ["deepseek-v4-flash"],
    },
    "qwen": {
        "flagship": ["qwen3.7-max", "qwen3-max"],
        "workhorse": ["qwen-flash", "qwen3.5-flash"],
    },
}

PROVIDER_AUTH_REQUIREMENTS = {
    # TRACKER_* preferred in the shared hub env (avoids colliding with exchange /
    # M0 tracer keys); unprefixed names remain valid for CI and local .env.
    "anthropic": {
        "env": ["TRACKER_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"],
        "purpose": "weekly task billing runs",
    },
    "openai": {
        "env": ["TRACKER_OPENAI_API_KEY", "OPENAI_API_KEY"],
        "purpose": "weekly task billing runs",
    },
    "google": {
        "env": ["TRACKER_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "purpose": "weekly task billing runs",
    },
    "xai": {
        "env": ["TRACKER_XAI_API_KEY", "XAI_API_KEY"],
        "purpose": "weekly task billing runs",
    },
    "aws": {
        "env": [
            "TRACKER_AMAZON_API_KEY",
            "TRACKER_AWS_BEARER_TOKEN_BEDROCK",
            "AWS_BEARER_TOKEN_BEDROCK",
            "AWS_ACCESS_KEY_ID",
        ],
        "purpose": "weekly task billing runs + tokenizer ledger (Bedrock Converse)",
    },
    "deepseek": {
        "env": ["TRACKER_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"],
        "purpose": "weekly task billing runs",
    },
    "qwen": {
        "env": ["TRACKER_QWEN_API_KEY", "QWEN_API_KEY"],
        "purpose": "weekly task billing runs",
    },
}


def _name_marks_deprecation(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in DEPRECATED_HINTS)


def _row_marks_deprecation(row: dict) -> bool:
    return (not row.get("is_active", True)) or _name_marks_deprecation(row.get("display_name"))


def build_models(series: list[dict], schema_by_date: dict[str, str]) -> list[dict]:
    """Per pricing_id lifecycle summary for the archive view.

    pricing_ids that only ever appeared under schema 1.x are dropped: the
    scraper rewrote model_ids when it moved to 2.x, so those entries are
    rename artifacts, not real model deprecations.
    """
    def is_legacy_schema(date: str) -> bool:
        return schema_by_date.get(date, "").startswith("1.")

    by_id: dict[str, list[dict]] = {}
    for row in series:
        by_id.setdefault(row["pricing_id"], []).append(row)

    latest_date = max((r["date"] for r in series), default=None)

    models: list[dict] = []
    for pid, rows in by_id.items():
        rows.sort(key=lambda r: r["date"])

        # Drop pricing_ids whose most recent appearance was under schema 1.x —
        # they're scraper-rewrite ghosts, not deprecations the user cares about.
        if is_legacy_schema(rows[-1]["date"]):
            continue

        first = rows[0]
        last = rows[-1]
        active_dates = [r["date"] for r in rows if r["is_active"]]
        flagged_dates = [r["date"] for r in rows if _row_marks_deprecation(r)]

        last_active = max(active_dates) if active_dates else None
        # first date where the row is explicitly flagged as inactive/deprecated/retired
        deprecated_on = min(flagged_dates) if flagged_dates else None

        currently_present = (last["date"] == latest_date)
        currently_active = currently_present and last["is_active"]

        # disappeared = was present in some snapshot but missing from latest
        disappeared_after = None if currently_present else last["date"]

        models.append({
            "pricing_id": pid,
            "provider_id": last["provider_id"],
            "model_id": last["model_id"],
            "display_name": last["display_name"],
            "category": last.get("category"),
            "first_seen": first["date"],
            "last_seen": last["date"],
            "last_active": last_active,
            "deprecated_on": deprecated_on,
            "disappeared_after": disappeared_after,
            "currently_present": currently_present,
            "currently_active": currently_active,
            "name_marks_deprecation": _name_marks_deprecation(last["display_name"]),
            "latest_input": last.get("input_price"),
            "latest_output": last.get("output_price"),
            "latest_cached_input": last.get("cached_input_price"),
            "currency": last.get("currency", "USD"),
        })

    models.sort(key=lambda m: (m["provider_id"], m["model_id"]))
    return models


def build_index(series: list[dict], schema_by_date: dict[str, str]) -> dict:
    dates = sorted({r["date"] for r in series})
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dates": dates,
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "snapshot_count": len(dates),
        "row_count": len(series),
        "schema_versions": schema_by_date,
        # Published so an empty chart can say *why* it is empty. Without it a
        # reader on the day the epoch moves sees a blank page and concludes the
        # site is broken, which is worse than the history the epoch hides.
        "dashboard_start_date": DASHBOARD_START_DATE,
    }


def _pick_tier_model(
    by_provider_model: dict[tuple[str, str], dict],
    provider_id: str,
    candidates: list[str],
) -> dict | None:
    for model_id in candidates:
        row = by_provider_model.get((provider_id, model_id))
        if row is None:
            continue
        input_price = row.get("latest_input", row.get("input_price"))
        output_price = row.get("latest_output", row.get("output_price"))
        if input_price is None or output_price is None:
            continue
        if not row.get("currently_active", row.get("is_active", True)):
            continue
        return row
    return None


# Rough chars-per-token used only for the *budget* estimate. Actual observed
# density is measured per provider; this constant never feeds the drift series.
BUDGET_CHARS_PER_TOKEN = 4

# Planning assumption for output length per task, used only for budgeting once the
# output cap was removed (policy 4.0.0). It is emphatically **not** a bound: with
# no cap there is no worst case to compute, because a model's maximum output is
# whatever the provider decides it is on any given day. Set from observation — the
# longest generation seen on the suite is ~4,000 on task C — and it should be
# revised when observation moves, not defended as a limit.
BUDGET_OUTPUT_TOKENS_PER_TASK = 4000


def _pack_cost(models: list[dict], tasks_by_id: dict[str, dict], task_ids: list[str]) -> float:
    """Planning list cost for one run: estimated input + assumed output length.

    Was a worst case while every task carried an output cap. Uncapped, the honest
    reading is an estimate: `BUDGET_OUTPUT_TOKENS_PER_TASK` stands in where a task
    has no cap, and the result bounds nothing.

    Task ids with no generating spec (task E is counted, never generated) cost
    nothing here and are skipped rather than treated as missing.
    """
    total = 0.0
    for task_id in task_ids:
        task = tasks_by_id.get(task_id)
        if task is None:
            continue
        est_input_tokens = task["input_chars"] / BUDGET_CHARS_PER_TOKEN
        for model in models:
            input_price = model.get("latest_input", model.get("input_price"))
            output_price = model.get("latest_output", model.get("output_price"))
            if input_price is None or output_price is None:
                continue
            output_tokens = task.get("output_cap") or BUDGET_OUTPUT_TOKENS_PER_TASK
            total += (est_input_tokens / 1_000_000) * input_price
            total += (output_tokens / 1_000_000) * output_price
    return total


def _load_equivalence_runs() -> list[dict]:
    if not EQUIVALENCE_RUNS_FILE.exists():
        return []
    payload = json.loads(EQUIVALENCE_RUNS_FILE.read_text())
    rows = payload.get("rows")
    return rows if isinstance(rows, list) else []


def _load_live_model_map() -> dict[tuple[str, str], dict]:
    if not LIVE_FILE.exists():
        return {}
    payload = json.loads(LIVE_FILE.read_text())
    live_rows = normalize_snapshot(payload, datetime.now(timezone.utc).date().isoformat())
    by_provider_model: dict[tuple[str, str], dict] = {}
    for row in live_rows:
        key = (row["provider_id"], row["model_id"])
        candidate = {
            "provider_id": row["provider_id"],
            "model_id": row["model_id"],
            "display_name": row.get("display_name") or row["model_id"],
            "input_price": row.get("input_price"),
            "output_price": row.get("output_price"),
            "currency": row.get("currency", "USD"),
            "is_active": row.get("is_active", True),
        }
        existing = by_provider_model.get(key)
        if existing is None:
            by_provider_model[key] = candidate
            continue
        existing_input = existing.get("input_price")
        existing_output = existing.get("output_price")
        incoming_input = candidate.get("input_price")
        incoming_output = candidate.get("output_price")
        existing_score = int(existing.get("is_active", False)) + int(existing_input is not None) + int(existing_output is not None)
        incoming_score = int(candidate.get("is_active", False)) + int(incoming_input is not None) + int(incoming_output is not None)
        if incoming_score > existing_score:
            by_provider_model[key] = candidate
    return by_provider_model


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def build_token_runs(run_rows: list[dict]) -> list[dict]:
    """Normalize observed run rows into the comparable per-task drift series.

    Workhorse replicates (N>1) are collapsed to the **median** of OK observations
    so the chart is not biased by a single noisy sample. Raw replicate counts are
    retained on each collapsed row as `replicate_count`.
    """
    chars_by_task = {task_id: len(prompt) for task_id, prompt in TASK_PROMPTS.items()}
    groups: dict[tuple, list[dict]] = {}

    for row in run_rows:
        if row.get("run_status") != "ok":
            continue
        tokens_in = row.get("tokens_in")
        tokens_out = row.get("tokens_out")
        if tokens_in is None or tokens_out is None:
            continue

        task_id = row.get("task_id")
        input_chars = row.get("input_chars") or chars_by_task.get(task_id)
        if not input_chars:
            continue

        run_date = run_date_of(row)
        if not include_dashboard_date(run_date):
            continue

        key = (run_date, row.get("provider_id"), row.get("tier"), task_id)
        groups.setdefault(key, []).append(row)

    out: list[dict] = []
    for (run_date, provider_id, tier, task_id), rows in groups.items():
        input_chars = rows[0].get("input_chars") or chars_by_task[task_id]
        tokens_in = _median([float(r["tokens_in"]) for r in rows])
        tokens_out = _median([float(r["tokens_out"]) for r in rows])
        ratios = [
            float(r["tokens_in"]) / ((float(r.get("input_chars") or chars_by_task[task_id])) / 1000)
            for r in rows
            if (r.get("input_chars") or chars_by_task.get(task_id))
        ]
        density = _median(ratios) if ratios else (tokens_in / (input_chars / 1000))
        output_cap = rows[0].get("output_cap")
        # Rows from policy 4.0.0 on carry the provider's own stop reason. The
        # cap comparison is the legacy path only, for rows written when a cap
        # was still sent; it reads as False once `output_cap` is null.
        censored = any(
            r.get("output_censored")
            if r.get("output_censored") is not None
            else (bool(output_cap) and r["tokens_out"] >= output_cap)
            for r in rows
        )
        usd_vals = [r.get("usd_value_same_day") for r in rows if r.get("usd_value_same_day") is not None]
        usd = _median(usd_vals) if usd_vals else None

        out.append(
            {
                "date": run_date,
                "provider_id": provider_id,
                "tier": tier,
                "task_id": task_id,
                "model_id": rows[0].get("model_id"),
                "api_model": rows[0].get("api_model"),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "tokens_total": tokens_in + tokens_out,
                "input_chars": input_chars,
                "tokens_in_per_1k_chars": round(density, 3),
                "usd": usd,
                "output_cap": output_cap,
                "output_censored": bool(censored),
                "replicate_count": len(rows),
                "corpus_version": rows[0].get("corpus_version", CORPUS_VERSION),
                # Absent on rows collected before the cap regime was versioned;
                # those are the tight per-task caps, i.e. policy 1.0.0.
                "output_policy_version": rows[0].get("output_policy_version", "1.0.0"),
            }
        )

    out.sort(key=lambda r: (r["date"] or "", r["provider_id"] or "", r["tier"] or "", r["task_id"] or ""))
    return out


def _tier_fallbacks(
    by_provider_model: dict, provider_id: str, tier_ids: list[str], chosen_model_id: str
) -> list[dict]:
    out = []
    for model_id in tier_ids:
        if model_id == chosen_model_id:
            continue
        row = by_provider_model.get((provider_id, model_id))
        if row is None:
            continue
        out.append(
            {
                "model_id": model_id,
                "input_price": row.get("latest_input", row.get("input_price")),
                "output_price": row.get("latest_output", row.get("output_price")),
            }
        )
    return out


def build_provider_health(
    selected: list[dict],
    run_rows: list[dict],
    ledger_rows: list[dict],
    wrapper_rows: list[dict],
) -> list[dict]:
    """Per-panel-row collection health, derived from observed runs.

    Env inspection cannot answer "is this provider reporting?" — a key can be
    present and every call still fail on a dead model id or a rejected
    parameter, which is exactly how `/tokens` went dark while the workflow kept
    reporting success. This reads the rows the runners actually wrote, so a
    silent provider outage is visible on the surface and to the ops gate.
    """
    # Health reports on the last collection that was *attempted*, so it is read
    # from the unfiltered rows rather than from the post-epoch view. Scoping it to
    # the epoch would make "we have not run the new cadence yet" indistinguishable
    # from "every provider is failing", which is the alarm this exists to raise.
    sources = []
    for name, rows, date_of in (
        ("meter", run_rows, run_date_of),
        ("ledger", ledger_rows, lambda row: row.get("date") or ""),
        ("wrapper", wrapper_rows, run_date_of),
    ):
        attempted = [row for row in rows if row.get("run_status") not in (None, "dry_run")]
        # Counts are scoped to the most recent collection for the source. All-time
        # counts would let a fresh regression hide behind last week's successes —
        # the whole point is to catch the run that just broke.
        latest = max((date_of(row) for row in attempted), default="") or None
        sources.append((name, attempted, date_of, latest))

    health = []
    for entry in selected:
        key = (entry["provider_id"], entry["tier"])
        item = {"provider_id": entry["provider_id"], "tier": entry["tier"], "sources": {}}
        for name, rows, date_of, latest in sources:
            mine = [row for row in rows if (row.get("provider_id"), row.get("tier")) == key]
            current = [row for row in mine if date_of(row) == latest]
            ok_rows = [row for row in current if row.get("run_status") == "ok"]
            unavailable_rows = [
                row for row in current if row.get("run_status") == "provider_unavailable"
            ]
            error_rows = [
                row
                for row in current
                if row.get("run_status")
                not in ("ok", "provider_unavailable", "dry_run", None, "missing_key")
            ]
            failed = unavailable_rows + error_rows
            last_error = max(failed, key=lambda r: r.get("run_at") or "", default=None)
            last_unavailable_remedy = None
            if last_error and last_error.get("run_status") == "provider_unavailable":
                last_unavailable_remedy = remedy_for_error(
                    last_error.get("error") or "",
                    last_error.get("provider_id"),
                )
            item["sources"][name] = {
                "latest_observed": latest,
                "ok_count": len(ok_rows),
                "error_count": len(error_rows),
                "unavailable_count": len(unavailable_rows),
                "last_ok": max(
                    (date_of(row) for row in mine if row.get("run_status") == "ok"),
                    default="",
                )
                or None,
                "last_error": (last_error or {}).get("error"),
                "last_error_model": (last_error or {}).get("api_model"),
                # The model the provider actually served, not the id the panel
                # asked for. Published so the ops gate can check that a
                # provider's two tiers resolved to two different models: Gemini
                # resolves its callable id at runtime, and a tier hint that got
                # dropped had google flagship counted on the workhorse model for
                # the entire ledger (D77). Nothing failed — both tiers reported
                # ok, with identical numbers, which reads as "shared tokenizer"
                # and is indistinguishable from the real thing by eye.
                "ok_models": sorted({row.get("api_model") for row in ok_rows if row.get("api_model")}),
                "last_unavailable_remedy": last_unavailable_remedy,
            }
        item["reporting"] = any(src["ok_count"] for src in item["sources"].values())
        item["dark_sources"] = sorted(
            name
            for name, src in item["sources"].items()
            if src["error_count"] and not src["ok_count"]
        )
        item["unavailable_sources"] = sorted(
            name
            for name, src in item["sources"].items()
            if src["unavailable_count"] and not src["ok_count"] and not src["error_count"]
        )
        health.append(item)
    return health


def build_ledger_fits(ledger_rows: list[dict]) -> list[dict]:
    """Split each day's ledger counts into fixed request overhead and content rate.

    `tokens_in_per_1k_chars` is not a tokenizer measurement on a short task. Every
    provider prepends a constant — chat template, system preamble, injected tool
    schema — and dividing that constant by the task's own character count makes the
    same overhead read as 4,248 tokens/1K chars on task A (157 chars) and 181 on
    task D (25,743). That is why the per-task charts are near-copies of each other:
    they are one provider constant rescaled by four divisors.

    So fit `tokens_in = fixed + rate * chars` across the day's tasks and publish the
    two parameters instead of the ratio. `rate` is the tokenizer signal, comparable
    across tasks because it is estimated across them; `fixed` is the scaffolding
    signal, which is where provider-side wrapper changes actually land.

    Only the two fitted parameters are published, never a per-task residual density.
    Task D carries 96% of the suite's characters, so it determines the slope, and the
    residual at a 157-character task divided by 0.157K chars swings between 4 and 23
    characters per token — implausible numbers that would invite exactly the
    misreading this fit exists to remove.
    """
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for row in ledger_rows:
        if row.get("run_status") != "ok":
            continue
        chars = row.get("input_chars")
        tokens = row.get("tokens_in")
        if not chars or tokens is None:
            continue
        key = (row.get("date") or "", row.get("provider_id") or "", row.get("tier") or "")
        groups.setdefault(key, []).append(row)

    def _guards(candidate: list[dict]) -> tuple[bool, float]:
        """Whether a task set can separate a constant from a slope, and its span.

        Fewer than three tasks cannot do it with any confidence, and a narrow
        character span cannot either: on 2026-08-21, before task D was collected,
        A/B/C span only 5.4x and the fitted rate lands 3% off the next day's
        four-task fit — a step change that is pure conditioning, not tokenization.
        """
        chars = [float(r["input_chars"]) for r in candidate]
        span = (max(chars) / min(chars)) if chars and min(chars) else 0.0
        return (len(candidate) >= MIN_FIT_TASKS and span >= MIN_FIT_CHAR_SPAN), span

    out = []
    for (date, provider_id, tier), rows in groups.items():
        # Which tasks the slope is allowed to rest on. The guards above are about
        # arithmetic identifiability, and task D satisfies them single-handedly: it
        # is 96% of the suite's characters, so it was long the only reason any day
        # cleared 10x. But task D is one sentence repeated 800 times (lexical
        # variety 0.007), so its marginal cost is the cost of re-merging that
        # phrase, not of tokenizing text — which is why 13 of 14 model rows read
        # 155.7 tokens/1K chars to within 0.2 and looked like agreement between
        # vocabularies that genuinely differ (D77).
        #
        # So prefer the non-degenerate tasks, and fall back to all of them when
        # they cannot be fitted. The fallback is not a formality: it is what the
        # whole pre-task-F record uses, where A/B/C alone span 5.4x. There it still
        # yields the overhead — the intercept is anchored by the short tasks, so a
        # wrong slope far out barely moves it, and overhead is the half with a
        # track record (grok-4.6, +430 tokens on every task at once) — while the
        # rate is withheld.
        # Keyed on the row's own corpus version, not just its task id: task D's
        # prompt was replaced in corpus 2.0.0, so a D row is filler or prose
        # depending on when it was collected, and the id cannot tell you which.
        natural = [
            r for r in rows if not is_degenerate(r.get("task_id"), r.get("corpus_version"))
        ]
        density_ok, natural_span = _guards(natural)
        if density_ok:
            fit_rows, span = natural, natural_span
        else:
            fit_rows, span = rows, _guards(rows)[1]

        points = [(float(r["input_chars"]), float(r["tokens_in"])) for r in fit_rows]
        fit_ok = _guards(fit_rows)[0]

        fixed = rate = r2 = None
        if fit_ok:
            n = len(points)
            sum_x = sum(p[0] for p in points)
            sum_y = sum(p[1] for p in points)
            sum_xx = sum(p[0] * p[0] for p in points)
            sum_xy = sum(p[0] * p[1] for p in points)
            denom = n * sum_xx - sum_x * sum_x
            if denom:
                rate = (n * sum_xy - sum_x * sum_y) / denom
                fixed = (sum_y - rate * sum_x) / n
                mean_y = sum_y / n
                ss_tot = sum((p[1] - mean_y) ** 2 for p in points)
                ss_res = sum((p[1] - (fixed + rate * p[0])) ** 2 for p in points)
                r2 = (1 - ss_res / ss_tot) if ss_tot else None
            else:
                fit_ok = False

        out.append(
            {
                "date": date,
                "provider_id": provider_id,
                "tier": tier,
                "model_id": rows[0].get("model_id"),
                "api_model": rows[0].get("api_model"),
                "task_ids": sorted({r.get("task_id") for r in rows if r.get("task_id")}),
                # Which tasks the two parameters were actually estimated from. Not
                # cosmetic: the basis changed when task F arrived, and an intercept
                # estimated with task D is not strictly the same quantity as one
                # estimated without it. Carrying it per row lets the chart break the
                # line where the basis changes, the way it already does for a model
                # change, instead of drawing the switch as provider drift.
                "fit_task_ids": sorted(
                    {r.get("task_id") for r in fit_rows if r.get("task_id")}
                ),
                "task_count": len(points),
                "char_span_ratio": round(span, 2),
                "fit_ok": bool(fit_ok),
                # Whether the slope rests on text a vocabulary can disagree about.
                # False for the whole pre-task-F record, where task D was the only
                # long task.
                "density_ok": bool(density_ok),
                # Tokens added regardless of payload size. The number that moved
                # when grok-4.6 gained 430 tokens on every task at once.
                "fixed_overhead_tokens": None if fixed is None else round(fixed, 1),
                # Marginal tokens per 1,000 characters of actual content.
                "content_density_per_1k_chars": (
                    None if (rate is None or not density_ok) else round(rate * 1000, 3)
                ),
                "r2": None if r2 is None else round(r2, 6),
            }
        )

    out.sort(key=lambda r: (r["date"] or "", r["provider_id"] or "", r["tier"] or ""))
    return out


def _load_json_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    if isinstance(payload, list):
        return payload
    return payload.get("rows", [])


# ---- estimated spend -------------------------------------------------------
#
# The costs block answers one question — "what did running this index cost on
# that day, at list" — and refuses to answer it when it cannot answer it fully.
# A day is published as complete only when every request the schedule promises
# came back priced, because a total assembled from most of a day is not a smaller
# number, it is an unknown one. `cost_events.json` is a replay-replaced canonical
# operational record, not an invoice ledger: it records what the runners did at
# list prices, and providers bill from their own meters.

# Mirrors the runner: task E is the one conversation, and its turn count is the
# number of frozen user prompts rather than a written-down 3, so adding a fourth
# prompt moves the completeness bar with it.
CONVERSATION_TASK_ID = "E"

# The scheduled daily run is one pass of the panel — flagship and workhorse at
# replicate 1. A canonical replicate 2 is a different run regime, not more
# evidence of this one, so it reads as an unexpected record rather than being
# folded into the day.
SCHEDULED_REPLICATE = 1


def _expected_requests() -> Counter:
    """What one panel row owes the record on a scheduled day.

    A Counter, not a set: the schedule promises each request exactly once, and
    two records of one request is a different failure from a missing one. Set
    membership cannot see either — it reads a doubled charge as satisfied and a
    replicate-2-instead-of-1 as present — and both mis-state the day's spend.

    The identity is (source, task, turn, replicate). Source belongs in it
    because the meter and the ledger both send task A: without it, a ledger
    probe would silently discharge the meter's obligation.
    """
    expected: Counter = Counter()
    for task_id in GENERATING_TASK_IDS:
        turns = (
            range(1, len(E_USER_PROMPTS) + 1)
            if task_id == CONVERSATION_TASK_ID
            else [None]
        )
        for turn in turns:
            expected[("meter", task_id, turn, SCHEDULED_REPLICATE)] += 1
    for task_id in LEDGER_TASK_IDS:
        expected[("ledger", task_id, None, SCHEDULED_REPLICATE)] += 1
    return expected


EXPECTED_REQUESTS = _expected_requests()

_TASK_ORDER = {task_id: position for position, task_id in enumerate(TASK_IDS)}
_SOURCE_ORDER = {"meter": 0, "ledger": 1}


def _request_sort_key(key: tuple) -> tuple:
    source, task_id, turn, replicate = key
    return (
        _SOURCE_ORDER.get(source, len(_SOURCE_ORDER)),
        _TASK_ORDER.get(task_id, len(_TASK_ORDER)),
        task_id,
        turn or 0,
        replicate,
    )


def _request_row(key: tuple, **extra) -> dict:
    source, task_id, turn, replicate = key
    return {
        "source": source,
        "task_id": task_id,
        "turn": turn,
        "replicate": replicate,
        **extra,
    }


def _expected_rows(source: str) -> list[dict]:
    keys = [key for key in EXPECTED_REQUESTS if key[0] == source]
    return [_request_row(key) for key in sorted(keys, key=_request_sort_key)]


def _request_diagnostics(observed: Counter, expected: Counter) -> dict:
    """Name every way the observed record departs from the schedule.

    Published rather than reduced to a boolean: "the day is incomplete" is not
    actionable, and the three cases have different causes — a provider outage, a
    replay that was not replaced, and a run under a regime the schedule does not
    describe.
    """
    missing = [key for key, count in expected.items() if observed[key] < count]
    duplicate = [
        key
        for key, count in expected.items()
        if observed[key] > count
    ]
    unexpected = [key for key in observed if key not in expected]
    return {
        "missing_requests": [
            _request_row(key) for key in sorted(missing, key=_request_sort_key)
        ],
        "duplicate_requests": [
            _request_row(key, observed_count=observed[key])
            for key in sorted(duplicate, key=_request_sort_key)
        ],
        "unexpected_requests": [
            _request_row(key) for key in sorted(unexpected, key=_request_sort_key)
        ],
    }


def _has_diagnostics(diagnostics: dict) -> bool:
    return any(diagnostics[name] for name in diagnostics)


def _cost_leaf(event: dict) -> dict:
    return {
        "event_id": event.get("event_id"),
        "source": event.get("source"),
        "request_kind": event.get("request_kind"),
        "task_id": event.get("task_id"),
        "turn": event.get("turn"),
        "attempt": event.get("attempt", 1),
        "replicate": event.get("replicate", 1),
        "api_model": event.get("api_model"),
        # False marks a request that was really made and really billed but is not
        # part of the canonical record for the day — an abandoned task E attempt.
        # It counts as spend and never counts toward completeness.
        "canonical": bool(event.get("canonical", True)),
        "complete": bool(event.get("complete")),
        "input_tokens": event.get("input_tokens"),
        "output_tokens": event.get("output_tokens"),
        "input_cost_usd": event.get("input_cost_usd"),
        "output_cost_usd": event.get("output_cost_usd"),
        "estimated_cost_usd": event.get("estimated_cost_usd"),
    }


def _leaf_request_key(leaf: dict) -> tuple:
    return (leaf["source"], leaf["task_id"], leaf["turn"], leaf["replicate"])


def _leaf_sort_key(leaf: dict) -> tuple:
    return (
        _SOURCE_ORDER.get(leaf.get("source"), len(_SOURCE_ORDER)),
        leaf.get("turn") or 0,
        leaf.get("replicate") or 0,
        leaf.get("attempt") or 0,
        leaf.get("event_id") or "",
    )


def _observed_requests(leaves: list[dict]) -> Counter:
    """Count the requests that can satisfy the schedule.

    Only complete canonical records qualify. An abandoned attempt is real spend
    on a request that was superseded, and a request whose price is unknown has
    not been accounted for at all — neither discharges an obligation.
    """
    return Counter(
        _leaf_request_key(leaf)
        for leaf in leaves
        if leaf["complete"] and leaf["canonical"]
    )


def _cost_totals(leaves: list[dict]) -> dict:
    """Split a set of leaves into the three published figures.

    The split is by source, not by request kind: everything the meter sends is a
    generation and splits into input and output, and everything the ledger sends
    — native counts and completion probes alike — is instrumentation that the
    index pays for without producing a measured generation.
    """
    generations = [leaf for leaf in leaves if leaf["source"] == "meter"]
    supporting = [leaf for leaf in leaves if leaf["source"] != "meter"]
    return {
        "estimated_spend_usd": sum(leaf["estimated_cost_usd"] or 0.0 for leaf in leaves),
        "input_cost_usd": sum(leaf["input_cost_usd"] or 0.0 for leaf in generations),
        "output_cost_usd": sum(leaf["output_cost_usd"] or 0.0 for leaf in generations),
        "supporting_cost_usd": sum(
            leaf["estimated_cost_usd"] or 0.0 for leaf in supporting
        ),
    }


def _build_cost_task(task_id: str, leaves: list[dict], on_panel: bool) -> dict:
    """One task inside one panel row.

    Completeness means the same thing here as it does at the day: every request
    the schedule names for this task, present exactly once and priced. A task
    that sums two of three billed turns is not a cheaper task, and a reader
    drilling into a withheld day must not find green rows inside it.
    """
    expected = Counter(
        {key: count for key, count in EXPECTED_REQUESTS.items() if key[1] == task_id}
    )
    diagnostics = (
        _request_diagnostics(_observed_requests(leaves), expected)
        if on_panel
        else {"missing_requests": [], "duplicate_requests": [], "unexpected_requests": []}
    )
    return {
        "task_id": task_id,
        **_cost_totals(leaves),
        "complete": all(leaf["complete"] for leaf in leaves)
        and not _has_diagnostics(diagnostics),
        **diagnostics,
        "details": leaves,
    }


def _build_cost_row(entry: dict | None, key: tuple[str, str], events: list[dict]) -> dict:
    provider_id, tier = key
    leaves = sorted((_cost_leaf(event) for event in events), key=_leaf_sort_key)
    on_panel = entry is not None

    by_task: dict[str, list[dict]] = {}
    for leaf in leaves:
        by_task.setdefault(leaf["task_id"], []).append(leaf)
    if on_panel:
        for _, task_id, _, _ in EXPECTED_REQUESTS:
            by_task.setdefault(task_id, [])

    tasks = [
        _build_cost_task(task_id, by_task[task_id], on_panel)
        for task_id in sorted(by_task, key=lambda t: _TASK_ORDER.get(t, len(_TASK_ORDER)))
    ]

    diagnostics = (
        _request_diagnostics(_observed_requests(leaves), EXPECTED_REQUESTS)
        if on_panel
        else {"missing_requests": [], "duplicate_requests": [], "unexpected_requests": []}
    )
    incomplete = [leaf for leaf in leaves if not leaf["complete"]]

    return {
        "provider_id": provider_id,
        "tier": tier,
        "model_id": (entry or {}).get("model_id"),
        "display_name": (entry or {}).get("display_name"),
        # False for a provider/tier that spent money without being on the panel —
        # an ad-hoc backfill, or a row dropped from the panel after it ran. Its
        # spend is real and stays in the totals; it is not owed to the schedule.
        "on_panel": on_panel,
        **_cost_totals(leaves),
        "complete": not _has_diagnostics(diagnostics) and not incomplete,
        **diagnostics,
        "incomplete_event_count": len(incomplete),
        "tasks": tasks,
    }


def _build_cost_day(date: str, events: list[dict], panel: list[dict]) -> dict:
    by_key: dict[tuple[str, str], list[dict]] = {}
    for event in events:
        by_key.setdefault((event.get("provider_id"), event.get("tier")), []).append(event)

    panel_by_key = {(entry["provider_id"], entry["tier"]): entry for entry in panel}
    off_panel = sorted(key for key in by_key if key not in panel_by_key)

    rows = [
        _build_cost_row(panel_by_key[key], key, by_key.get(key, []))
        for key in panel_by_key
    ]
    rows += [_build_cost_row(None, key, by_key[key]) for key in off_panel]

    leaves = [leaf for row in rows for task in row["tasks"] for leaf in task["details"]]
    incomplete_count = sum(row["incomplete_event_count"] for row in rows)
    panel_rows = [row for row in rows if row["on_panel"]]

    def gather(name: str) -> list[dict]:
        return [
            {"provider_id": row["provider_id"], "tier": row["tier"], **request}
            for row in panel_rows
            for request in row[name]
        ]

    return {
        "date": date,
        # A day is complete when the whole panel reported exactly what the
        # schedule names and nothing anywhere is unpriced. The second half
        # matters as much as the first: one request without a price makes the
        # day's real spend unknown, not merely lower.
        "complete": bool(panel_rows)
        and all(row["complete"] for row in panel_rows)
        and incomplete_count == 0,
        **_cost_totals(leaves),
        "event_count": len(leaves),
        "incomplete_event_count": incomplete_count,
        "missing_requests": gather("missing_requests"),
        "duplicate_requests": gather("duplicate_requests"),
        "unexpected_requests": gather("unexpected_requests"),
        "provider_tiers": rows,
    }


def _clamp_to_month(year: int, month: int, day: int) -> Date:
    """The same ordinal day in another month, or that month's last day.

    31 March has no counterpart in February, and 29 February has none in a common
    year. Clamping keeps the compared window a real calendar range instead of
    silently spilling into the following month.
    """
    return Date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _window_summary(
    daily_index: dict[str, dict], start: Date, end: Date, floor: Date
) -> dict | None:
    """Total a date range, and say whether the range is fully accounted for.

    Returns None when the range lies entirely before the completeness floor —
    that is an absent period, which reads differently from a period that was
    scheduled and came back short.
    """
    window_start = max(start, floor)
    if end < window_start:
        return None

    amount = 0.0
    scheduled = 0
    complete = 0
    day = window_start
    while day <= end:
        scheduled += 1
        entry = daily_index.get(day.isoformat())
        if entry is not None and entry["complete"]:
            complete += 1
            amount += entry["estimated_spend_usd"]
        day += timedelta(days=1)

    clipped = window_start > start
    return {
        "start_date": window_start.isoformat(),
        "end_date": end.isoformat(),
        "scheduled_date_count": scheduled,
        "complete_date_count": complete,
        "amount_usd": amount,
        "complete": complete == scheduled,
        # True when the epoch cut days off the front of the calendar period. The
        # window is then shorter than the one it would be compared against, so
        # its total is not a like-for-like figure even when it is fully reported.
        "clipped": clipped,
        "clip_reason": "before_complete_start_date" if clipped else None,
    }


def build_cost_comparison(current: dict | None, previous: dict | None) -> dict:
    """One period against its predecessor, refusing every comparison it cannot make.

    `new_baseline` means there is nothing comparable to compare against — the
    prior period is entirely before the epoch, or it really did spend nothing,
    which no percentage can express. `comparison_unavailable` means a comparison
    was owed and cannot be trusted: a window came back short, or the epoch cut
    the prior window down to fewer days than the current one covers.

    The current amount is published whenever the current window is whole, even
    when no comparison can be drawn: what is withheld is the delta, not the
    money. Both windows carry their scheduled and complete day counts so the
    card can say *why* rather than only that.
    """
    def window_facts(window: dict | None) -> dict | None:
        if window is None:
            return None
        return {
            key: window[key]
            for key in (
                "start_date",
                "end_date",
                "scheduled_date_count",
                "complete_date_count",
                "complete",
                "clipped",
                "clip_reason",
            )
        }

    result = {
        "status": "comparison_unavailable",
        "reason": None,
        "amount_usd": None,
        "previous_amount_usd": None,
        "delta_usd": None,
        "delta_pct": None,
        "current_window": window_facts(current),
        "previous_window": window_facts(previous),
    }

    if current is None or not current["complete"]:
        result["reason"] = "current_window_incomplete"
        result["previous_window"] = None
        return result

    result["amount_usd"] = current["amount_usd"]

    if previous is None or not previous["scheduled_date_count"]:
        result["status"] = "new_baseline"
        result["reason"] = "no_comparable_prior_period"
        result["previous_window"] = None
        return result

    if previous["clipped"]:
        result["reason"] = "previous_window_clipped"
        return result

    if not previous["complete"]:
        result["reason"] = "previous_window_incomplete"
        return result

    previous_amount = previous["amount_usd"]
    result["previous_amount_usd"] = previous_amount
    result["delta_usd"] = current["amount_usd"] - previous_amount
    if previous_amount:
        result["status"] = "ok"
        result["delta_pct"] = result["delta_usd"] / previous_amount * 100.0
    else:
        # Nothing was spent in the prior window, so there is no ratio to state.
        result["status"] = "new_baseline"
        result["reason"] = "zero_previous_amount"
    return result


def build_costs(
    events: list[dict],
    selected_models: list[dict],
    latest_attempt_date: str | None = None,
) -> dict:
    """Daily estimated spend for the panel, with period comparisons.

    `selected_models` is the panel the meter actually runs — the mode-two tier
    rows — so the completeness bar follows the panel instead of a written-down
    row count. `latest_attempt_date` widens `latest_attempted_date` to a day the
    runners attempted without writing cost events; it must come from collection,
    never from the pricing scrape, which runs on its own schedule and would
    otherwise report an attempt that never happened.
    """
    panel = [
        {
            "provider_id": entry["provider_id"],
            "tier": entry["tier"],
            "model_id": entry.get("model_id"),
            "display_name": entry.get("display_name"),
        }
        for entry in selected_models
    ]

    by_date: dict[str, list[dict]] = {}
    for event in events or []:
        date = event.get("date") or ""
        if not include_dashboard_date(date):
            continue
        by_date.setdefault(date, []).append(event)

    daily = [_build_cost_day(date, rows, panel) for date, rows in sorted(by_date.items())]
    daily_index = {day["date"]: day for day in daily}
    complete_dates = [day["date"] for day in daily if day["complete"]]
    latest_complete = complete_dates[-1] if complete_dates else None

    attempted = list(by_date)
    if latest_attempt_date and include_dashboard_date(latest_attempt_date):
        attempted.append(latest_attempt_date)

    floor = Date.fromisoformat(DASHBOARD_START_DATE)
    comparisons: dict[str, dict] = {}
    if latest_complete is None:
        comparisons = {
            period: build_cost_comparison(None, None)
            for period in ("current_run", "current_month", "year_to_date")
        }
    else:
        latest = Date.fromisoformat(latest_complete)

        # Run over run: the last complete day against the day scheduled before
        # it. Deliberately the previous *scheduled* day and not the previous day
        # that happened to report — skipping a failed day would compare two runs
        # 48 hours apart and label the result a run-over-run change.
        previous_day = latest - timedelta(days=1)
        comparisons["current_run"] = build_cost_comparison(
            _window_summary(daily_index, latest, latest, floor),
            _window_summary(daily_index, previous_day, previous_day, floor),
        )

        # Month to date against the same stretch of the month before it.
        previous_month_year = latest.year if latest.month > 1 else latest.year - 1
        previous_month = latest.month - 1 if latest.month > 1 else 12
        comparisons["current_month"] = build_cost_comparison(
            _window_summary(daily_index, Date(latest.year, latest.month, 1), latest, floor),
            _window_summary(
                daily_index,
                Date(previous_month_year, previous_month, 1),
                _clamp_to_month(previous_month_year, previous_month, latest.day),
                floor,
            ),
        )

        # Year to date against the same stretch of the year before it.
        comparisons["year_to_date"] = build_cost_comparison(
            _window_summary(daily_index, Date(latest.year, 1, 1), latest, floor),
            _window_summary(
                daily_index,
                Date(latest.year - 1, 1, 1),
                _clamp_to_month(latest.year - 1, latest.month, latest.day),
                floor,
            ),
        )

    return {
        "complete_start_date": DASHBOARD_START_DATE,
        "latest_attempted_date": max(attempted, default=None),
        "latest_complete_date": latest_complete,
        "status": "active" if latest_complete else "pending_first_complete_day",
        "panel_row_count": len(panel),
        "expected_meter_requests_per_row": _expected_rows("meter"),
        "expected_ledger_requests_per_row": _expected_rows("ledger"),
        "complete_date_count": len(complete_dates),
        "note": (
            "Estimated spend at list prices, computed from the tokens each request "
            "actually reported. It is not an invoice: providers bill from their own "
            "meters, and a day is published only once every scheduled meter and "
            "ledger request came back priced, exactly once."
        ),
        "comparisons": comparisons,
        "daily": daily,
    }


# ---- cost publication ------------------------------------------------------
#
# The request tree is roughly two orders of magnitude larger than the figures
# the Costs cards need, and it is only ever read one date at a time. So
# `equivalence.json` — fetched on every page load — carries the comparisons and
# a per-day summary, and the tree is written to a static file per date that the
# page fetches when a date is selected. `cost_events.json` stays as it is: the
# raw operational record, not a second copy of the published summary.

COST_DETAIL_DIRNAME = "costs"
COST_DETAIL_DIR = DASHBOARD_DATA_DIR / COST_DETAIL_DIRNAME
_COST_DETAIL_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")

_COST_TOTAL_FIELDS = (
    "estimated_spend_usd",
    "input_cost_usd",
    "output_cost_usd",
    "supporting_cost_usd",
)


def _detail_path(date: str) -> str:
    """Where the page fetches one date's tree, relative to `dashboard/data/`."""
    return f"{COST_DETAIL_DIRNAME}/{date}.json"


def _diagnostic_counts(node: dict) -> dict:
    return {
        "missing_request_count": len(node["missing_requests"]),
        "duplicate_request_count": len(node["duplicate_requests"]),
        "unexpected_request_count": len(node["unexpected_requests"]),
    }


def summarize_costs(costs: dict) -> dict:
    """The costs block as published inline: totals and counts, no request leaves."""
    daily = []
    for day in costs["daily"]:
        daily.append(
            {
                "date": day["date"],
                "complete": day["complete"],
                **{field: day[field] for field in _COST_TOTAL_FIELDS},
                "event_count": day["event_count"],
                "incomplete_event_count": day["incomplete_event_count"],
                **_diagnostic_counts(day),
                "detail_path": _detail_path(day["date"]),
                "provider_tiers": [
                    {
                        "provider_id": row["provider_id"],
                        "tier": row["tier"],
                        "model_id": row["model_id"],
                        "display_name": row["display_name"],
                        "on_panel": row["on_panel"],
                        "complete": row["complete"],
                        **{field: row[field] for field in _COST_TOTAL_FIELDS},
                        "incomplete_event_count": row["incomplete_event_count"],
                        **_diagnostic_counts(row),
                    }
                    for row in day["provider_tiers"]
                ],
            }
        )

    summary = {key: value for key, value in costs.items() if key != "daily"}
    summary["detail_index_path"] = f"{COST_DETAIL_DIRNAME}/index.json"
    summary["daily"] = daily
    return summary


def write_cost_details(costs: dict, directory: Path = COST_DETAIL_DIR) -> dict:
    """Write one static tree per date plus an index, and drop stale dates.

    Cost events are replay-replaced, so a date can legitimately leave the
    record; a file left behind would keep answering fetches for a day the record
    no longer contains. Only generated `YYYY-MM-DD.json` files are removed.
    """
    directory.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    written: set[str] = set()
    for day in costs["daily"]:
        path = directory / f"{day['date']}.json"
        written.add(path.name)
        path.write_text(
            json.dumps({"generated_at": generated_at, **day}, indent=2) + "\n"
        )

    index = {
        "generated_at": generated_at,
        "complete_start_date": costs["complete_start_date"],
        "latest_attempted_date": costs["latest_attempted_date"],
        "latest_complete_date": costs["latest_complete_date"],
        "dates": [
            {
                "date": day["date"],
                "complete": day["complete"],
                "estimated_spend_usd": day["estimated_spend_usd"],
                "path": _detail_path(day["date"]),
            }
            for day in costs["daily"]
        ],
    }
    (directory / "index.json").write_text(json.dumps(index, indent=2) + "\n")

    for path in directory.glob("*.json"):
        if path.name in written or not _COST_DETAIL_FILE.match(path.name):
            continue
        path.unlink()

    return index


def build_equivalence(
    models: list[dict],
    index: dict,
    live_model_map: dict[tuple[str, str], dict] | None = None,
    cost_events: list[dict] | None = None,
) -> dict:
    by_provider_model_history = {
        (row["provider_id"], row["model_id"]): row
        for row in models
    }
    effective_live_map = _load_live_model_map() if live_model_map is None else live_model_map
    by_provider_model = {**by_provider_model_history, **effective_live_map}

    selected = []
    selected_by_mode = {"two": [], "three": []}
    for provider_id, tiers in TIER_CANDIDATES.items():
        for tier_name in TIER_ORDER:
            tier_ids = tiers.get(tier_name, [])
            row = _pick_tier_model(by_provider_model, provider_id, tier_ids)
            if row is None:
                continue
            entry = {
                "provider_id": provider_id,
                "tier": tier_name,
                "model_id": row["model_id"],
                "display_name": row["display_name"],
                "input_price": row.get("latest_input", row.get("input_price")),
                "output_price": row.get("latest_output", row.get("output_price")),
                "currency": row.get("currency", "USD"),
                # The rest of the tier's preference list, priced. A pinned model
                # can be in the catalog and still uncallable (Legacy on the key,
                # absent from the Bedrock region), so runners fall through in
                # this order and value the row at the price of the model that
                # actually answered.
                "api_candidates": _tier_fallbacks(
                    by_provider_model, provider_id, tier_ids, row["model_id"]
                ),
            }
            selected.append(entry)
            selected_by_mode["two"].append(entry)
            selected_by_mode["three"].append(entry)

    tasks_by_id = {task["task_id"]: task for task in TASK_DEFINITIONS}
    budgets = {"two": {}, "three": {}}
    for mode, mode_models in selected_by_mode.items():
        for pack_id, task_ids in TASK_PACKS.items():
            per_run = _pack_cost(mode_models, tasks_by_id, task_ids)
            budgets[mode][pack_id] = {
                "per_run_usd": per_run,
                "annual_usd": per_run * DAILY_RUNS_PER_YEAR,
                # `/pricing` prices the alternative cadences side by side so the
                # shipped one can be compared against what it replaced. It reads
                # this map, which the builder previously never wrote — the whole
                # table rendered as em-dashes.
                "annual_usd_by_cadence": {
                    name: per_run * runs for name, runs in RUNS_PER_YEAR_BY_CADENCE.items()
                },
            }

    # Planning figure for the shipped cadence: one run of every panel row at the
    # assumed output length. There is deliberately no worst case any more — output
    # is uncapped (policy 4.0.0), so the upper bound is whatever maximum each
    # provider applies, which is not a number we can know in advance.
    meter_models = selected_by_mode["two"]
    planning_per_run = _pack_cost(meter_models, tasks_by_id, TASK_PACKS["suiteLong"])
    planning_annual = planning_per_run * DAILY_RUNS_PER_YEAR

    auth = {
        provider: {
            "env": meta["env"],
            "purpose": meta["purpose"],
            "configured": any(bool(os.getenv(key)) for key in meta["env"]),
        }
        for provider, meta in PROVIDER_AUTH_REQUIREMENTS.items()
    }
    missing = [provider for provider, item in auth.items() if not item["configured"]]
    run_rows = _load_equivalence_runs()
    ledger_all = _load_json_rows(TOKENIZER_LEDGER_FILE)
    wrapper_all = _load_json_rows(WRAPPER_RUNS_FILE)
    provider_health = build_provider_health(selected, run_rows, ledger_all, wrapper_all)
    dark = sorted(
        f"{item['provider_id']}·{item['tier']}" for item in provider_health if not item["reporting"]
    )
    # `live_runs` is collection telemetry — the last time the runner wrote rows —
    # so like health it reads the unfiltered rows.
    latest_date = max((run_date_of(row) for row in run_rows), default="") or None
    latest_rows = [row for row in run_rows if run_date_of(row) == latest_date] if latest_date else []

    # Everything charted or tabulated starts at the epoch.
    ledger_rows = [row for row in ledger_all if include_dashboard_date(row.get("date") or "")]
    ledger_ok = [row for row in ledger_rows if row.get("run_status") == "ok"]
    # The wrapper archive is retired but still drawn, so it obeys the same start
    # line as everything else: the page must not show history it says it is not
    # showing. The rows that survive the epoch are read only as the historical
    # wrapper series — never as task E, which is now a generated three-turn
    # conversation rather than a counted transcript.
    wrapper_rows = [row for row in wrapper_all if include_dashboard_date(run_date_of(row))]
    wrapper_ok = [row for row in wrapper_rows if row.get("run_status") == "ok"]

    # What collection last *attempted*, from the runners themselves. The pricing
    # scrape is a separate pipeline on its own schedule, so `index["last_date"]`
    # would report a cost-collection attempt on a day nothing ran.
    latest_attempt_date = max(
        (
            date
            for date in (
                [run_date_of(row) for row in run_rows]
                + [row.get("date") or "" for row in ledger_all]
            )
            if include_dashboard_date(date)
        ),
        default=None,
    )
    costs = build_costs(
        load_cost_events() if cost_events is None else cost_events,
        selected_by_mode["two"],
        latest_attempt_date,
    )

    return {
        "generated_at": index["generated_at"],
        "pricing_snapshot_date": index.get("last_date"),
        "corpus_version": CORPUS_VERSION,
        "chat_corpus_version": CHAT_CORPUS_VERSION,
        "output_policy_version": OUTPUT_POLICY_VERSION,
        "output_ceiling": OUTPUT_CEILING,
        "dashboard_start_date": DASHBOARD_START_DATE,
        "tiers": list(TIER_ORDER),
        "tasks": TASK_DEFINITIONS,
        # The canonical corpus, and the two schedules cut from it. Published as
        # three explicit lists so a surface never has to infer which tasks are
        # generated and which are only counted.
        "task_ids": list(TASK_IDS),
        "generating_task_ids": list(GENERATING_TASK_IDS),
        "ledger_task_ids": list(LEDGER_TASK_IDS),
        "meter_task_ids": list(METER_TASK_IDS),
        # Task E as it is collected now: a generated conversation of frozen user
        # turns. `chat_task`/`chat_transcript` below describe the retired counted
        # transcript and are only for reading the wrapper archive.
        "conversation_task": {
            "task_id": CONVERSATION_TASK_ID,
            "turns": len(E_USER_PROMPTS),
            "prompts": list(E_USER_PROMPTS),
            "chat_corpus_version": CHAT_CORPUS_VERSION,
        },
        # Comparisons and per-day totals only. The request tree for a date is
        # fetched from `costs/<date>.json` when the reader asks for it.
        "costs": summarize_costs(costs),
        "chat_task": CHAT_TASK,
        "task_packs": TASK_PACKS,
        "chat_transcript": CHAT_TRANSCRIPT,
        "runs_per_year": DAILY_RUNS_PER_YEAR,
        "selected_models": selected,
        "selected_models_by_mode": selected_by_mode,
        "budget": budgets,
        # No ceiling exists under output policy 4.0.0. Kept as an explicit null
        # rather than dropped, so a reader of the artifact sees that the bound was
        # removed on purpose instead of wondering where the field went.
        "package_ceiling_annual_usd": None,
        "package_planning_annual_usd": planning_annual,
        "package_cost_note": (
            "Shipped package: daily generated meter on tasks A–F with flagship N=1 + "
            "workhorse N=1, daily tokenizer ledger on tasks A/B/C/D/F. "
            f"Planning figure is ${planning_annual:,.0f}/yr at list, assuming "
            f"{BUDGET_OUTPUT_TOKENS_PER_TASK:,} output tokens per task per day. Output is "
            "uncapped, so this bounds nothing: verbosity is the measurement, and a cap "
            "high enough to be safe is also high enough to eventually truncate the "
            "reading. Runs that a provider does cut short are flagged from its own stop "
            "reason."
        ),
        "token_runs": build_token_runs(run_rows),
        "tokenizer_ledger": {
            "cadence": "daily",
            "status": "active" if ledger_ok else "pending_first_run",
            # No density claim on the surface: a per-task ratio is a provider
            # constant rescaled by the task's own length (D77). Fixed request
            # overhead is the half that survives the fit, and it is read from
            # `fits` below rather than described here.
            "note": (
                "Count-only probes on the frozen corpus. Tasks A/B/C/D/F are counted "
                "daily, and each day's counts are fitted to separate fixed request "
                "overhead from payload size."
            ),
            "last_observed_date": max((r.get("date") for r in ledger_ok), default=None),
            "row_count": len(ledger_rows),
            "ok_row_count": len(ledger_ok),
            "rows": ledger_ok,
            # Per (date, provider, tier): the day's counts split into fixed request
            # overhead and marginal content rate, so density can be read without
            # the task's own length setting its scale. See build_ledger_fits.
            "fits": build_ledger_fits(ledger_ok),
        },
        "wrapper_runs": {
            "cadence": "historical",
            "status": "retired",
            "note": (
                "Historical wrapper archive: frozen 10-turn transcript prompt-token counts "
                "from the retired wrapper schedule. Kept for continuity; no longer collected. "
                "Not task E — E is now a generated three-turn conversation."
            ),
            # Clipped to the epoch like every other published series: see the
            # comment where these rows are read.
            "epoch_scoped": True,
            "chat_corpus_version": CHAT_CORPUS_VERSION,
            "last_date": max((run_date_of(r) for r in wrapper_ok), default="") or None,
            "row_count": len(wrapper_rows),
            "ok_row_count": len(wrapper_ok),
            "rows": wrapper_ok,
        },
        "provider_auth": {
            "requirements": auth,
            "missing_providers": missing,
            # Env-derived, and this builder normally runs in a step without the
            # provider keys — so it says nothing about whether collection works.
            # `provider_health` is the signal to trust.
            "env_visible_to_builder": bool(auth) and len(missing) < len(auth),
            "ready_for_live_runs": not dark,
        },
        "provider_health": {
            "panel": provider_health,
            "reporting_count": sum(1 for item in provider_health if item["reporting"]),
            "panel_count": len(provider_health),
            "dark": dark,
        },
        "live_runs": {
            "latest_date": latest_date,
            "latest_row_count": len(latest_rows),
            "total_row_count": len(run_rows),
            "latest_rows": latest_rows,
            # Read off the rows rather than restated, so it cannot drift from the
            # replicate count the workflow actually passes the runner.
            "workhorse_replicates": max(
                (r.get("replicate", 1) for r in latest_rows if r.get("tier") == "workhorse"),
                default=1,
            ),
        },
        "window": {
            "first_date": index.get("first_date"),
            "last_date": index.get("last_date"),
            "snapshot_count": index.get("snapshot_count"),
        },
    }


# ---- modes -----------------------------------------------------------------

def _publish_cost_details(equivalence: dict) -> dict:
    """Write the per-date trees that the summary in `equivalence.json` points at.

    Rebuilt from the same record and the same panel the summary was built from —
    both read back off the artifact — so the inline summary and the files it
    links to cannot describe different days.
    """
    costs = build_costs(
        load_cost_events(),
        equivalence["selected_models_by_mode"]["two"],
        equivalence["costs"]["latest_attempted_date"],
    )
    return write_cost_details(costs)


def cmd_rebuild() -> int:
    if not HISTORY_DIR.exists():
        print(f"error: {HISTORY_DIR} does not exist. Run backfill_pricing_history.py first.", file=sys.stderr)
        return 1
    DASHBOARD_DATA_DIR.mkdir(parents=True, exist_ok=True)

    series: list[dict] = []
    schema_by_date: dict[str, str] = {}

    snapshot_files = sorted(p for p in HISTORY_DIR.glob("*.json"))
    if not snapshot_files:
        print(f"error: no snapshots in {HISTORY_DIR}", file=sys.stderr)
        return 1

    for path in snapshot_files:
        date = path.stem  # YYYY-MM-DD
        if not include_dashboard_date(date):
            continue
        try:
            snap = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"  skip {path.name}: {e}", file=sys.stderr)
            continue
        schema_by_date[date] = snap.get("meta", {}).get("schema_version", "")
        rows = normalize_snapshot(snap, date)
        series.extend(rows)
        print(f"  {date}: {len(rows)} rows (schema {schema_by_date[date]})")

    models = build_models(series, schema_by_date)
    index = build_index(series, schema_by_date)
    equivalence = build_equivalence(models, index)

    SERIES_FILE.write_text(json.dumps(series) + "\n")
    MODELS_FILE.write_text(json.dumps(models, indent=2) + "\n")
    INDEX_FILE.write_text(json.dumps(index, indent=2) + "\n")
    EQUIVALENCE_FILE.write_text(json.dumps(equivalence, indent=2) + "\n")

    detail_index = _publish_cost_details(equivalence)

    print(f"\nWrote {SERIES_FILE.name} ({len(series)} rows)")
    print(f"Wrote {MODELS_FILE.name} ({len(models)} pricing_ids)")
    print(f"Wrote {INDEX_FILE.name} ({index['snapshot_count']} dates)")
    print(f"Wrote {EQUIVALENCE_FILE.name} ({len(equivalence['selected_models'])} selected tier rows)")
    print(f"Wrote {COST_DETAIL_DIRNAME}/ ({len(detail_index['dates'])} cost dates)")
    return 0


def cmd_append(date: str | None) -> int:
    if not LIVE_FILE.exists():
        print(f"error: {LIVE_FILE} not found", file=sys.stderr)
        return 1
    DASHBOARD_DATA_DIR.mkdir(parents=True, exist_ok=True)

    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not include_dashboard_date(date):
        print(f"skip {date}: before dashboard start date {DASHBOARD_START_DATE}")
        return 0

    snap = json.loads(LIVE_FILE.read_text())
    new_rows = normalize_snapshot(snap, date)

    if SERIES_FILE.exists():
        series = json.loads(SERIES_FILE.read_text())
    else:
        series = []
    series = [r for r in series if include_dashboard_date(r["date"])]

    # replace any existing rows for `date` (idempotent re-runs)
    series = [r for r in series if r["date"] != date]
    series.extend(new_rows)
    series.sort(key=lambda r: (r["date"], r["provider_id"], r["pricing_id"]))

    if INDEX_FILE.exists():
        prior_index = json.loads(INDEX_FILE.read_text())
        schema_by_date = {
            d: schema
            for d, schema in prior_index.get("schema_versions", {}).items()
            if include_dashboard_date(d)
        }
    else:
        schema_by_date = {}
    schema_by_date[date] = snap.get("meta", {}).get("schema_version", "")

    models = build_models(series, schema_by_date)
    index = build_index(series, schema_by_date)
    equivalence = build_equivalence(models, index)

    SERIES_FILE.write_text(json.dumps(series) + "\n")
    MODELS_FILE.write_text(json.dumps(models, indent=2) + "\n")
    INDEX_FILE.write_text(json.dumps(index, indent=2) + "\n")
    EQUIVALENCE_FILE.write_text(json.dumps(equivalence, indent=2) + "\n")

    detail_index = _publish_cost_details(equivalence)

    print(f"Appended {len(new_rows)} rows for {date} (total {len(series)} rows, {len(models)} pricing_ids)")
    print(f"Wrote {EQUIVALENCE_FILE.name} ({len(equivalence['selected_models'])} selected tier rows)")
    print(f"Wrote {COST_DETAIL_DIRNAME}/ ({len(detail_index['dates'])} cost dates)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build dashboard artifacts from pricing history.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--rebuild", action="store_true", help="rebuild artifacts from all pricing_history/*.json")
    g.add_argument("--append", action="store_true", help="append today's pricing.json into existing artifacts")
    ap.add_argument("--date", help="override the date (YYYY-MM-DD) for --append; defaults to today UTC")
    args = ap.parse_args()

    if args.rebuild:
        return cmd_rebuild()
    return cmd_append(args.date)


if __name__ == "__main__":
    sys.exit(main())
