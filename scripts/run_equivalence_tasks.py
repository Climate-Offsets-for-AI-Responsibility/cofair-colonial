#!/usr/bin/env python3
"""Run weekly token-equivalence tasks across configured providers.

Writes dashboard/data/equivalence_runs.json with one row per
(run_week, task_id, provider_id, tier, mode).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from task_corpus import CORPUS_VERSION, TASK_PROMPTS, TASK_SPECS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SHARED_ENV = (REPO_ROOT.parent / "cofair" / ".env" / ".env.cofair")
load_dotenv()
if DEFAULT_SHARED_ENV.exists():
    load_dotenv(DEFAULT_SHARED_ENV, override=False)
DATA_DIR = REPO_ROOT / "dashboard" / "data"
EQUIVALENCE_FILE = DATA_DIR / "equivalence.json"
RUNS_FILE = DATA_DIR / "equivalence_runs.json"

TIMEOUT_SECONDS = 90

QWEN_MODEL_MAP = {
    "qwen3.7-max": "qwen-max",
    "qwen3.7-plus": "qwen-plus",
    "qwen-flash": "qwen-turbo",
}

_GOOGLE_MODELS_CACHE: list[str] | None = None


def now_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def current_week_anchor(today: date | None = None) -> str:
    d = today or datetime.now(timezone.utc).date()
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def env_for_provider(provider_id: str) -> str | None:
    # Shared resolver: prefers TRACKER_* from the hub env, falls back to
    # unprefixed CI / local names. Kept inline so this runner stays runnable
    # without importing the count adapter.
    if provider_id == "google":
        return (
            os.getenv("TRACKER_GEMINI_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )
    if provider_id == "aws":
        return (
            os.getenv("TRACKER_AMAZON_API_KEY")
            or os.getenv("TRACKER_AWS_BEARER_TOKEN_BEDROCK")
            or os.getenv("AWS_BEARER_TOKEN_BEDROCK")
            or os.getenv("AWS_ACCESS_KEY_ID")
        )
    mapping = {
        "anthropic": ("TRACKER_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        "openai": ("TRACKER_OPENAI_API_KEY", "OPENAI_API_KEY"),
        "xai": ("TRACKER_XAI_API_KEY", "XAI_API_KEY"),
        "deepseek": ("TRACKER_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
        "qwen": ("TRACKER_QWEN_API_KEY", "QWEN_API_KEY"),
    }
    keys = mapping.get(provider_id)
    if not keys:
        return None
    for key_name in keys:
        val = os.getenv(key_name)
        if val:
            return val
    return None


BEDROCK_MODEL_MAP = {
    "nova-micro": "amazon.nova-micro-v1:0",
    "nova-lite": "amazon.nova-lite-v1:0",
    "nova-pro": "amazon.nova-pro-v1:0",
    "nova-premier": "us.amazon.nova-premier-v1:0",
    "nova-2.0-lite": "us.amazon.nova-2-lite-v1:0",
    "nova-2-lite": "us.amazon.nova-2-lite-v1:0",
    "nova-2.0-pro": "us.amazon.nova-2-pro-v1:0",
    "nova-2-pro": "us.amazon.nova-2-pro-v1:0",
}


def bedrock_region() -> str:
    return os.getenv("BEDROCK_REGION") or os.getenv("AWS_REGION") or "us-east-1"


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
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
        },
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


def google_model_for_tier(tier: str, api_key: str) -> str:
    models = available_google_models(api_key)
    if tier == "flagship":
        for candidate in models:
            if "pro" in candidate and "gemini" in candidate:
                return candidate
    for candidate in models:
        if "flash" in candidate and "gemini" in candidate:
            return candidate
    for candidate in models:
        if "gemini" in candidate:
            return candidate
    return "gemini-2.0-flash"


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


def resolve_api_model(provider_id: str, model_id: str, tier: str, api_key: str | None = None) -> str:
    if provider_id == "qwen":
        return QWEN_MODEL_MAP.get(model_id, model_id)
    if provider_id == "aws":
        return BEDROCK_MODEL_MAP.get(model_id, model_id)
    if provider_id == "google":
        if api_key:
            return google_model_for_tier(tier, api_key)
    return model_id


def run_provider_task(
    provider_id: str,
    model_id: str,
    tier: str,
    task_id: str,
    max_tokens: int,
    dry_run: bool,
) -> tuple[str, int | None, int | None, str | None, str]:
    prompt = TASK_PROMPTS[task_id]
    if dry_run:
        api_model = resolve_api_model(provider_id, model_id, tier, api_key=None)
        return "dry_run", None, None, None, api_model

    api_key = env_for_provider(provider_id)
    if not api_key:
        api_model = resolve_api_model(provider_id, model_id, tier, api_key=None)
        return "missing_key", None, None, "provider API key missing", api_model

    api_model = resolve_api_model(provider_id, model_id, tier, api_key=api_key)
    try:
        if provider_id == "anthropic":
            tokens_in, tokens_out = run_anthropic(api_model, prompt, max_tokens, api_key)
        elif provider_id == "google":
            last_error = None
            chosen_model = api_model
            for candidate in google_candidates_for_tier(tier, api_key):
                chosen_model = candidate
                api_model = candidate
                try:
                    tokens_in, tokens_out = run_gemini(candidate, prompt, max_tokens, api_key)
                    api_model = chosen_model
                    break
                except requests.HTTPError as exc:
                    last_error = exc
                    if exc.response is not None and exc.response.status_code in (404, 403):
                        continue
                    raise
            else:
                if last_error is not None:
                    raise last_error
                raise RuntimeError("No callable Gemini model candidates found")
        elif provider_id == "openai":
            tokens_in, tokens_out = run_openai_compatible(
                "https://api.openai.com/v1", api_model, prompt, max_tokens, api_key
            )
        elif provider_id == "xai":
            tokens_in, tokens_out = run_openai_compatible(
                "https://api.x.ai/v1", api_model, prompt, max_tokens, api_key
            )
        elif provider_id == "deepseek":
            tokens_in, tokens_out = run_openai_compatible(
                "https://api.deepseek.com", api_model, prompt, max_tokens, api_key
            )
        elif provider_id == "qwen":
            tokens_in, tokens_out = run_openai_compatible(
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                api_model,
                prompt,
                max_tokens,
                api_key,
            )
        elif provider_id == "aws":
            tokens_in, tokens_out = run_bedrock(api_model, prompt, max_tokens, api_key)
        else:
            return "unsupported_provider", None, None, f"provider {provider_id} not implemented", api_model
    except requests.HTTPError as exc:
        detail = None
        if exc.response is not None:
            try:
                detail = exc.response.text[:500]
            except Exception:  # noqa: BLE001
                detail = None
        msg = f"{exc}"
        if detail:
            msg = f"{msg} :: {detail}"
        msg = re.sub(r"(key=)[^&\\s]+", r"\\1[REDACTED]", msg)
        return "error", None, None, msg, api_model
    except Exception as exc:  # noqa: BLE001
        return "error", None, None, str(exc), api_model

    return "ok", tokens_in, tokens_out, None, api_model


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
    ap = argparse.ArgumentParser(description="Run weekly token-equivalence tasks.")
    ap.add_argument("--mode", choices=["two", "three"], default="two")
    ap.add_argument("--week", help="Override run week anchor (YYYY-MM-DD)")
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

    run_week = args.week or current_week_anchor()
    existing = load_existing_runs()
    keep = []
    replace_keys = set()
    wh_reps = max(1, args.workhorse_replicates)

    new_rows = []
    for model in models:
        replicates = wh_reps if model["tier"] == "workhorse" else 1
        for replicate in range(1, replicates + 1):
            for task_id in ("A", "B", "C", "D"):
                task = tasks[task_id]
                output_cap = int(task.get("output_cap") or task.get("output_tokens"))
                status, tokens_in, tokens_out, error, used_model = run_provider_task(
                    provider_id=model["provider_id"],
                    model_id=model["model_id"],
                    tier=model["tier"],
                    task_id=task_id,
                    max_tokens=output_cap,
                    dry_run=args.dry_run,
                )
                usd = usd_value(tokens_in, tokens_out, model["input_price"], model["output_price"])
                implied = implied_per_million(tokens_in, tokens_out, usd)
                input_chars = len(TASK_PROMPTS[task_id])

                row = {
                    "run_week": run_week,
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
                    "run_status": status,
                    "error": error,
                    "input_price": model["input_price"],
                    "output_price": model["output_price"],
                    "usd_value_same_day": usd,
                    "implied_cost_per_1m": implied,
                    "run_at": now_iso_z(),
                }
                new_rows.append(row)
                replace_keys.add(
                    (run_week, args.mode, task_id, model["provider_id"], model["tier"], replicate)
                )

    for row in existing:
        key = (
            row.get("run_week"),
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
            r["run_week"],
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
                "run_week": run_week,
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
