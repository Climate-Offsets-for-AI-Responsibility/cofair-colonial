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
from task_corpus import (  # noqa: E402
    CHAT_CORPUS_VERSION,
    CHAT_TRANSCRIPT,
    CORPUS_VERSION,
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
DASHBOARD_START_DATE = "2026-06-17"


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
    return date >= DASHBOARD_START_DATE


# ---- aggregation -----------------------------------------------------------

DEPRECATED_HINTS = ("deprecated", "retired", "legacy")

# Two tiers only: flagship and workhorse. The middle "default" tier was dropped
# because it mostly tracked the flagship and doubled inference spend without
# adding a distinct signal.
TIER_ORDER = ("flagship", "workhorse")

# The index runs on one fixed weekly cadence so every task is directly
# comparable week over week.
WEEKLY_RUNS_PER_YEAR = 52

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


# Rough chars-per-token used only for the *budget* upper bound. Actual observed
# density is measured per provider; this constant never feeds the drift series.
BUDGET_CHARS_PER_TOKEN = 4


def _pack_cost(models: list[dict], tasks_by_id: dict[str, dict], task_ids: list[str]) -> float:
    """Worst-case list cost for one run: estimated input + fully-spent output cap."""
    total = 0.0
    for task_id in task_ids:
        task = tasks_by_id[task_id]
        est_input_tokens = task["input_chars"] / BUDGET_CHARS_PER_TOKEN
        for model in models:
            input_price = model.get("latest_input", model.get("input_price"))
            output_price = model.get("latest_output", model.get("output_price"))
            if input_price is None or output_price is None:
                continue
            total += (est_input_tokens / 1_000_000) * input_price
            total += (task["output_cap"] / 1_000_000) * output_price
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

        key = (row.get("run_week"), row.get("provider_id"), row.get("tier"), task_id)
        groups.setdefault(key, []).append(row)

    out: list[dict] = []
    for (week, provider_id, tier, task_id), rows in groups.items():
        input_chars = rows[0].get("input_chars") or chars_by_task[task_id]
        tokens_in = _median([float(r["tokens_in"]) for r in rows])
        tokens_out = _median([float(r["tokens_out"]) for r in rows])
        output_cap = rows[0].get("output_cap")
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
                "week": week,
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
            }
        )

    out.sort(key=lambda r: (r["week"] or "", r["provider_id"] or "", r["tier"] or "", r["task_id"] or ""))
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
            row = _pick_tier_model(by_provider_model, provider_id, tiers.get(tier_name, []))
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
                "annual_usd": per_run * WEEKLY_RUNS_PER_YEAR,
            }

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
    latest_week = max((row.get("run_week") for row in run_rows if row.get("run_week")), default=None)
    latest_rows = [row for row in run_rows if row.get("run_week") == latest_week] if latest_week else []

    ledger_rows = _load_json_rows(TOKENIZER_LEDGER_FILE)
    ledger_ok = [row for row in ledger_rows if row.get("run_status") == "ok"]
    wrapper_rows = _load_json_rows(WRAPPER_RUNS_FILE)
    wrapper_ok = [row for row in wrapper_rows if row.get("run_status") == "ok"]

    return {
        "generated_at": index["generated_at"],
        "corpus_version": CORPUS_VERSION,
        "chat_corpus_version": CHAT_CORPUS_VERSION,
        "tiers": list(TIER_ORDER),
        "tasks": TASK_DEFINITIONS,
        "task_packs": TASK_PACKS,
        "chat_transcript": CHAT_TRANSCRIPT,
        "runs_per_year": WEEKLY_RUNS_PER_YEAR,
        "selected_models": selected,
        "selected_models_by_mode": selected_by_mode,
        "budget": budgets,
        "package_cost_note": (
            "Recommended package (~$40–45/yr list): weekly meter with flagship N=1 + "
            "workhorse N=3, daily ABC tokenizer ledger, weekly D ledger count, weekly "
            "Test 4 wrapper counts on turns 1–10."
        ),
        "token_runs": build_token_runs(run_rows),
        "tokenizer_ledger": {
            "cadence": "daily_ABC_weekly_D",
            "status": "active" if ledger_ok else "pending_first_run",
            "note": (
                "Count-only density on the frozen corpus. ABC collected daily; "
                "task D counted weekly with the meter week."
            ),
            "last_observed_date": max((r.get("date") for r in ledger_ok), default=None),
            "row_count": len(ledger_rows),
            "ok_row_count": len(ledger_ok),
            "rows": ledger_ok,
        },
        "wrapper_runs": {
            "cadence": "weekly",
            "status": "active" if wrapper_ok else "pending_first_run",
            "note": (
                "Test 4: frozen 10-turn transcript; api_prompt_tokens at turns 1–10. "
                "Assistant turns are never regenerated."
            ),
            "chat_corpus_version": CHAT_CORPUS_VERSION,
            "last_week": max((r.get("run_week") for r in wrapper_ok), default=None),
            "row_count": len(wrapper_rows),
            "ok_row_count": len(wrapper_ok),
            "rows": wrapper_ok,
        },
        "provider_auth": {
            "requirements": auth,
            "missing_providers": missing,
            "ready_for_live_runs": not missing,
        },
        "live_runs": {
            "latest_week": latest_week,
            "latest_row_count": len(latest_rows),
            "total_row_count": len(run_rows),
            "latest_rows": latest_rows,
            "workhorse_replicates": 3,
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
