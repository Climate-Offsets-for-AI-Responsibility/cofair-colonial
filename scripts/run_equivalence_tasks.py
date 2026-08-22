#!/usr/bin/env python3
"""Run the daily token-equivalence tasks across configured providers.

Writes dashboard/data/equivalence_runs.json with one row per
(run_date, task_id, provider_id, tier, mode).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NamedTuple

import requests
from dotenv import load_dotenv

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from provider_token_count import (  # noqa: E402
    _is_model_unavailable,
    api_model_candidates,
    bedrock_region,
    env_for_provider,
    openai_compatible_body,
)
from task_corpus import (  # noqa: E402
    CORPUS_VERSION,
    METER_TASK_IDS,
    OUTPUT_POLICY_VERSION,
    TASK_PROMPTS,
    TASK_SPECS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SHARED_ENV = (REPO_ROOT.parent / "cofair" / ".env" / ".env.cofair")
load_dotenv()
if DEFAULT_SHARED_ENV.exists():
    load_dotenv(DEFAULT_SHARED_ENV, override=False)
DATA_DIR = REPO_ROOT / "dashboard" / "data"
EQUIVALENCE_FILE = DATA_DIR / "equivalence.json"
RUNS_FILE = DATA_DIR / "equivalence_runs.json"

TIMEOUT_SECONDS = 90

_GOOGLE_MODELS_CACHE: list[str] | None = None


def now_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def current_run_date(today: date | None = None) -> str:
    """The UTC day this run belongs to.

    The meter used to snap to the ISO week's Monday. On a daily cadence that
    anchor is actively harmful: rows are replaced on a key that includes it, so
    every run in a week would overwrite the last and the record would still show
    one observation per week without ever saying so.
    """
    return (today or datetime.now(timezone.utc).date()).isoformat()


def run_anthropic(model: str, prompt: str, max_tokens: int, api_key: str) -> tuple[int, int]:
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    usage = payload.get("usage", {})
    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def run_openai_compatible(
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    api_key: str,
) -> tuple[int, int]:
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=openai_compatible_body(
            base_url, model, [{"role": "user", "content": prompt}], max_tokens
        ),
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    usage = payload.get("usage", {})
    return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


def run_gemini(model: str, prompt: str, max_tokens: int, api_key: str) -> tuple[int, int]:
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0},
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    usage = payload.get("usageMetadata", {})
    return int(usage.get("promptTokenCount", 0)), int(usage.get("candidatesTokenCount", 0))


def run_bedrock(model: str, prompt: str, max_tokens: int, api_key: str) -> tuple[int, int]:
    region = bedrock_region()
    response = requests.post(
        f"https://bedrock-runtime.{region}.amazonaws.com/model/{model}/converse",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    usage = response.json().get("usage", {})
    return int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))


def available_google_models(api_key: str) -> list[str]:
    global _GOOGLE_MODELS_CACHE
    if _GOOGLE_MODELS_CACHE is not None:
        return _GOOGLE_MODELS_CACHE
    response = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": api_key},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    models = []
    for item in payload.get("models", []):
        methods = item.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        name = item.get("name", "")
        if not name.startswith("models/"):
            continue
        models.append(name.replace("models/", ""))
    _GOOGLE_MODELS_CACHE = models
    return models


def google_candidates_for_tier(tier: str, api_key: str) -> list[str]:
    models = available_google_models(api_key)
    models = [
        model
        for model in models
        if "gemini" in model
        and "tts" not in model
        and "embedding" not in model
        and "aqa" not in model
        and "vision" not in model
        and "imagen" not in model
    ]
    ranked = []
    if tier == "flagship":
        ranked.extend([m for m in models if "pro" in m])
    ranked.extend([m for m in models if "flash" in m])
    ranked.extend([m for m in models if m not in ranked])
    if not ranked:
        ranked = ["gemini-2.0-flash"]
    return ranked


OPENAI_COMPATIBLE_BASES = {
    "openai": "https://api.openai.com/v1",
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}


def tier_candidates(
    provider_id: str,
    model_id: str,
    tier: str,
    api_key: str | None,
    fallback_model_ids: list[str] | None = None,
) -> list[str]:
    """Ordered api model ids to try for this panel row.

    Google is resolved from the account's live model list; every other provider
    walks its own pinned id first, then the rest of the tier's preference list.
    """
    if provider_id == "google" and api_key:
        return google_candidates_for_tier(tier, api_key)
    return api_model_candidates(provider_id, model_id, tier, api_key, fallback_model_ids)


def resolve_api_model(provider_id: str, model_id: str, tier: str, api_key: str | None = None) -> str:
    candidates = tier_candidates(provider_id, model_id, tier, api_key)
    return candidates[0] if candidates else model_id


def candidate_plan(
    provider_id: str, tier: str, entry: dict, api_key: str | None
) -> list[tuple[str, float | None, float | None]]:
    """Preference-ordered (api_model, input_price, output_price) for a panel row.

    Prices ride along so a row that falls through to a different model is valued
    at that model's rate card rather than the pinned one's.
    """
    own = (entry.get("input_price"), entry.get("output_price"))
    if provider_id == "google" and api_key:
        return [(m, *own) for m in google_candidates_for_tier(tier, api_key)]

    plan: list[tuple[str, float | None, float | None]] = []
    seen: set[str] = set()
    for row in [entry, *(entry.get("api_candidates") or [])]:
        prices = (row.get("input_price"), row.get("output_price"))
        for api_model in api_model_candidates(provider_id, row["model_id"], tier, api_key):
            if api_model in seen:
                continue
            seen.add(api_model)
            plan.append((api_model, *prices))
    return plan


def _run_one(provider_id: str, api_model: str, prompt: str, max_tokens: int, api_key: str):
    if provider_id == "anthropic":
        return run_anthropic(api_model, prompt, max_tokens, api_key)
    if provider_id == "google":
        return run_gemini(api_model, prompt, max_tokens, api_key)
    if provider_id in OPENAI_COMPATIBLE_BASES:
        return run_openai_compatible(
            OPENAI_COMPATIBLE_BASES[provider_id], api_model, prompt, max_tokens, api_key
        )
    if provider_id == "aws":
        return run_bedrock(api_model, prompt, max_tokens, api_key)
    raise LookupError(f"provider {provider_id} not implemented")


class TaskResult(NamedTuple):
    status: str
    tokens_in: int | None
    tokens_out: int | None
    error: str | None
    api_model: str
    input_price: float | None
    output_price: float | None


def run_provider_task(entry: dict, task_id: str, max_tokens: int, dry_run: bool) -> TaskResult:
    provider_id = entry["provider_id"]
    model_id = entry["model_id"]
    tier = entry["tier"]
    prompt = TASK_PROMPTS[task_id]
    own_prices = (entry.get("input_price"), entry.get("output_price"))

    if dry_run:
        api_model = resolve_api_model(provider_id, model_id, tier, api_key=None)
        return TaskResult("dry_run", None, None, None, api_model, *own_prices)

    api_key = env_for_provider(provider_id)
    if not api_key:
        api_model = resolve_api_model(provider_id, model_id, tier, api_key=None)
        return TaskResult(
            "missing_key", None, None, "provider API key missing", api_model, *own_prices
        )

    plan = candidate_plan(provider_id, tier, entry, api_key)
    api_model = plan[0][0] if plan else model_id
    last_error: str | None = None
    for candidate, input_price, output_price in plan:
        api_model = candidate
        try:
            tokens_in, tokens_out = _run_one(provider_id, candidate, prompt, max_tokens, api_key)
        except LookupError as exc:
            return TaskResult(
                "unsupported_provider", None, None, str(exc), candidate, *own_prices
            )
        except requests.HTTPError as exc:
            detail = None
            if exc.response is not None:
                try:
                    detail = exc.response.text[:500]
                except Exception:  # noqa: BLE001
                    detail = None
            last_error = re.sub(
                r"(key=)[^&\s]+", r"\1[REDACTED]", f"{exc} :: {detail}" if detail else f"{exc}"
            )
            # A pinned id can be absent from the catalog, Legacy on this key, or
            # missing in the region; those justify the next candidate. Anything
            # else is a real fault and must surface on the first attempt.
            if _is_model_unavailable(exc):
                continue
            return TaskResult("error", None, None, last_error, candidate, *own_prices)
        except Exception as exc:  # noqa: BLE001
            return TaskResult("error", None, None, str(exc), candidate, *own_prices)
        return TaskResult("ok", tokens_in, tokens_out, None, candidate, input_price, output_price)

    return TaskResult(
        "error",
        None,
        None,
        last_error or "no callable model candidate",
        api_model,
        *own_prices,
    )


def usd_value(tokens_in: int | None, tokens_out: int | None, input_price: float, output_price: float) -> float | None:
    if tokens_in is None or tokens_out is None:
        return None
    return (tokens_in / 1_000_000) * input_price + (tokens_out / 1_000_000) * output_price


def implied_per_million(tokens_in: int | None, tokens_out: int | None, usd: float | None) -> float | None:
    if usd is None or tokens_in is None or tokens_out is None:
        return None
    total = tokens_in + tokens_out
    if total <= 0:
        return None
    return usd * (1_000_000 / total)


def load_equivalence() -> dict:
    return json.loads(EQUIVALENCE_FILE.read_text())


def load_existing_runs() -> list[dict]:
    if not RUNS_FILE.exists():
        return []
    payload = json.loads(RUNS_FILE.read_text())
    return payload.get("rows", [])


def save_runs(rows: list[dict]) -> None:
    RUNS_FILE.write_text(
        json.dumps(
            {
                "generated_at": now_iso_z(),
                "row_count": len(rows),
                "rows": rows,
            },
            indent=2,
        )
        + "\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the daily token-equivalence tasks.")
    ap.add_argument("--mode", choices=["two", "three"], default="two")
    ap.add_argument("--date", help="Override the run date (YYYY-MM-DD); defaults to today UTC")
    ap.add_argument("--dry-run", action="store_true", help="Do not call providers; emit dry_run statuses.")
    ap.add_argument("--limit-models", type=int, default=0, help="Optional model row limit for smoke runs.")
    ap.add_argument(
        "--workhorse-replicates",
        type=int,
        default=3,
        help="Replicates for workhorse tier (flagship always 1). Default 3.",
    )
    ap.add_argument(
        "--provider",
        choices=["anthropic", "openai", "google", "xai", "aws", "deepseek", "qwen"],
        help="Run for one provider only.",
    )
    args = ap.parse_args()

    eq = load_equivalence()
    tasks = {task["task_id"]: task for task in eq["tasks"]}
    models = list(eq["selected_models_by_mode"][args.mode])
    if args.provider:
        models = [model for model in models if model["provider_id"] == args.provider]
    if args.limit_models and args.limit_models > 0:
        models = models[: args.limit_models]

    run_date = args.date or current_run_date()
    existing = load_existing_runs()
    keep = []
    replace_keys = set()
    wh_reps = max(1, args.workhorse_replicates)

    new_rows = []
    for model in models:
        replicates = wh_reps if model["tier"] == "workhorse" else 1
        for replicate in range(1, replicates + 1):
            for task_id in METER_TASK_IDS:
                task = tasks[task_id]
                output_cap = int(task.get("output_cap") or task.get("output_tokens"))
                result = run_provider_task(model, task_id, output_cap, args.dry_run)
                status = result.status
                tokens_in, tokens_out = result.tokens_in, result.tokens_out
                error, used_model = result.error, result.api_model
                usd = usd_value(tokens_in, tokens_out, result.input_price, result.output_price)
                implied = implied_per_million(tokens_in, tokens_out, usd)
                input_chars = len(TASK_PROMPTS[task_id])

                row = {
                    "run_date": run_date,
                    "mode": args.mode,
                    "task_id": task_id,
                    "provider_id": model["provider_id"],
                    "tier": model["tier"],
                    "replicate": replicate,
                    "model_id": model["model_id"],
                    "api_model": used_model,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "output_cap": output_cap,
                    "output_censored": (
                        tokens_out is not None and tokens_out >= output_cap
                    ),
                    "input_chars": input_chars,
                    "corpus_version": CORPUS_VERSION,
                    "output_policy_version": OUTPUT_POLICY_VERSION,
                    "run_status": status,
                    "error": error,
                    "input_price": result.input_price,
                    "output_price": result.output_price,
                    "usd_value_same_day": usd,
                    "implied_cost_per_1m": implied,
                    "run_at": now_iso_z(),
                }
                new_rows.append(row)
                replace_keys.add(
                    (run_date, args.mode, task_id, model["provider_id"], model["tier"], replicate)
                )

    for row in existing:
        key = (
            row.get("run_date"),
            row.get("mode"),
            row.get("task_id"),
            row.get("provider_id"),
            row.get("tier"),
            row.get("replicate", 1),
        )
        if key in replace_keys:
            continue
        keep.append(row)

    merged = keep + new_rows
    merged.sort(
        key=lambda r: (
            r.get("run_date") or "",
            r["provider_id"],
            r["tier"],
            r.get("replicate", 1),
            r["task_id"],
        )
    )
    save_runs(merged)

    ok = sum(1 for row in new_rows if row["run_status"] == "ok")
    print(
        json.dumps(
            {
                "event": "equivalence_runs_written",
                "run_date": run_date,
                "mode": args.mode,
                "workhorse_replicates": wh_reps,
                "dry_run": args.dry_run,
                "rows_written": len(new_rows),
                "ok_rows": ok,
                "output_file": str(RUNS_FILE),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
