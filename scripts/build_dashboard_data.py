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
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "ops"))
from provider_faults import remedy_for_error  # noqa: E402
from task_corpus import (  # noqa: E402
    CHAT_CORPUS_VERSION,
    CHAT_TASK,
    CHAT_TRANSCRIPT,
    CORPUS_VERSION,
    METER_TASK_IDS,
    OUTPUT_CEILING,
    OUTPUT_POLICY_VERSION,
    TASK_DEFINITIONS,
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
DASHBOARD_START_DATE = "2026-08-24"


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
                "tokens_in_per_1k_chars": round(tokens_in / (input_chars / 1000), 3),
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

    out = []
    for (date, provider_id, tier), rows in groups.items():
        points = [(float(r["input_chars"]), float(r["tokens_in"])) for r in rows]
        chars = [p[0] for p in points]
        span = (max(chars) / min(chars)) if min(chars) else 0.0

        # Two guards, both about identifiability rather than tidiness. Fewer than
        # three tasks cannot separate a constant from a slope with any confidence,
        # and a narrow character span cannot either: on 2026-08-21, before task D
        # was collected, A/B/C span only 5.4x and the fitted rate lands 3% off the
        # next day's four-task fit — a step change that is pure conditioning, not
        # tokenization. Publishing it would manufacture the false positive this
        # measure is meant to retire.
        fit_ok = len(points) >= MIN_FIT_TASKS and span >= MIN_FIT_CHAR_SPAN

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
                "task_count": len(points),
                "char_span_ratio": round(span, 2),
                "fit_ok": bool(fit_ok),
                # Tokens added regardless of payload size. The number that moved
                # when grok-4.6 gained 430 tokens on every task at once.
                "fixed_overhead_tokens": None if fixed is None else round(fixed, 1),
                # Marginal tokens per 1,000 characters of actual content.
                "content_density_per_1k_chars": None if rate is None else round(rate * 1000, 3),
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


def build_equivalence(
    models: list[dict],
    index: dict,
    live_model_map: dict[tuple[str, str], dict] | None = None,
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
    wrapper_rows = [row for row in wrapper_all if include_dashboard_date(run_date_of(row))]
    wrapper_ok = [row for row in wrapper_rows if row.get("run_status") == "ok"]

    return {
        "generated_at": index["generated_at"],
        "corpus_version": CORPUS_VERSION,
        "chat_corpus_version": CHAT_CORPUS_VERSION,
        "output_policy_version": OUTPUT_POLICY_VERSION,
        "output_ceiling": OUTPUT_CEILING,
        "dashboard_start_date": DASHBOARD_START_DATE,
        "tiers": list(TIER_ORDER),
        "tasks": TASK_DEFINITIONS,
        "meter_task_ids": list(METER_TASK_IDS),
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
            "Shipped package: daily meter with flagship N=1 + workhorse N=1, daily "
            "tokenizer ledger on tasks A–D, daily task E wrapper counts on turns 1–10. "
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
            "note": (
                "Count-only density on the frozen corpus. Tasks A\u2013D all counted "
                "daily."
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
            "cadence": "daily",
            "status": "active" if wrapper_ok else "pending_first_run",
            "note": (
                "Test 4: frozen 10-turn transcript; api_prompt_tokens at turns 1–10. "
                "Assistant turns are never regenerated."
            ),
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

    print(f"\nWrote {SERIES_FILE.name} ({len(series)} rows)")
    print(f"Wrote {MODELS_FILE.name} ({len(models)} pricing_ids)")
    print(f"Wrote {INDEX_FILE.name} ({index['snapshot_count']} dates)")
    print(f"Wrote {EQUIVALENCE_FILE.name} ({len(equivalence['selected_models'])} selected tier rows)")
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

    print(f"Appended {len(new_rows)} rows for {date} (total {len(series)} rows, {len(models)} pricing_ids)")
    print(f"Wrote {EQUIVALENCE_FILE.name} ({len(equivalence['selected_models'])} selected tier rows)")
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
