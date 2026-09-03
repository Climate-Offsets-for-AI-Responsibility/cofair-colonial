#!/usr/bin/env python3
"""Daily/weekly tokenizer ledger — count-only density on the frozen corpus.

Writes dashboard/data/tokenizer_ledger.json.
Recommended cadence: --tasks ABC daily; --tasks D weekly (with the meter).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provider_token_count import (  # noqa: E402
    api_model_candidates,
    count_prompt_tokens_text,
    env_for_provider,
)
from cost_events import (  # noqa: E402
    build_cost_event,
    load_cost_events,
    merge_cost_events,
    save_cost_events,
)
from task_corpus import (  # noqa: E402
    CORPUS_VERSION,
    LEDGER_TASK_IDS,
    TASK_PROMPTS,
    TASK_SPECS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SHARED_ENV = REPO_ROOT.parent / "cofair" / ".env" / ".env.cofair"
load_dotenv()
if DEFAULT_SHARED_ENV.exists():
    load_dotenv(DEFAULT_SHARED_ENV, override=False)

DATA_DIR = REPO_ROOT / "dashboard" / "data"
EQUIVALENCE_FILE = DATA_DIR / "equivalence.json"
LEDGER_FILE = DATA_DIR / "tokenizer_ledger.json"

# The literal sets are kept for ad-hoc runs, but `all` is derived, and the daily
# workflow uses it. A hard-coded "ABCD" in a workflow file is how a task can be
# added to the corpus, published on the dashboard, and then never collected —
# which for task F would have left content density withheld forever, with nothing
# failing anywhere to say why.
TASK_SETS = {
    "ABC": ["A", "B", "C"],
    "D": ["D"],
    "ABCD": ["A", "B", "C", "D"],
    "all": list(LEDGER_TASK_IDS),
}


def now_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_panel(mode: str = "two", eq: dict | None = None) -> list[dict]:
    eq = eq or load_equivalence()
    return list(eq["selected_models_by_mode"][mode])


def load_equivalence() -> dict:
    return json.loads(EQUIVALENCE_FILE.read_text())


def load_ledger() -> list[dict]:
    if not LEDGER_FILE.exists():
        return []
    payload = json.loads(LEDGER_FILE.read_text())
    return payload.get("rows", [])


def save_ledger(rows: list[dict]) -> None:
    LEDGER_FILE.write_text(
        json.dumps(
            {
                "generated_at": now_iso_z(),
                "corpus_version": CORPUS_VERSION,
                "row_count": len(rows),
                "rows": rows,
            },
            indent=2,
        )
        + "\n"
    )


def prices_for_api_model(model: dict, api_model: str) -> tuple[float | None, float | None]:
    provider_id = model.get("provider_id")
    tier = model.get("tier")
    if not provider_id or not tier:
        return None, None

    for candidate in [model, *(model.get("api_candidates") or [])]:
        candidate_model = candidate.get("model_id")
        if not candidate_model:
            continue
        candidate_models = api_model_candidates(provider_id, candidate_model, tier, api_key=None)
        if api_model in candidate_models:
            return candidate.get("input_price"), candidate.get("output_price")
        if provider_id == "anthropic":
            dashed = str(candidate_model).replace(".", "-")
            if api_model == dashed or api_model.startswith(f"{dashed}-"):
                return candidate.get("input_price"), candidate.get("output_price")
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Run tokenizer ledger (count-only).")
    ap.add_argument("--tasks", choices=sorted(TASK_SETS), default="ABC")
    ap.add_argument("--date", help="Override observation date YYYY-MM-DD")
    ap.add_argument("--mode", choices=["two", "three"], default="two")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--provider", help="Limit to one provider_id")
    args = ap.parse_args()

    obs_date = args.date or today_utc()
    task_ids = TASK_SETS[args.tasks]
    equivalence = load_equivalence()
    pricing_snapshot_date = equivalence.get("pricing_snapshot_date")
    models = load_panel(args.mode, equivalence)
    if args.provider:
        models = [m for m in models if m["provider_id"] == args.provider]

    existing = load_ledger()
    replace = set()
    executed_event_groups: set[tuple[str, str, str, str, str]] = set()
    new_rows: list[dict] = []
    incoming_cost_events: list[dict] = []
    run_id = f"{obs_date}:{args.mode}:ledger"

    for model in models:
        for task_id in task_ids:
            prompt = TASK_PROMPTS[task_id]
            input_chars = len(prompt)
            api_key = env_for_provider(model["provider_id"])
            candidates = api_model_candidates(
                model["provider_id"],
                model["model_id"],
                model["tier"],
                api_key,
                model.get("api_candidates"),
            )
            api_model = candidates[0] if candidates else model["model_id"]
            usage = None

            if args.dry_run:
                status, error = "dry_run", None
            elif not api_key:
                status, error = "missing_key", "provider API key missing"
            else:
                status, usage, error, api_model = count_prompt_tokens_text(
                    model["provider_id"],
                    api_model,
                    prompt,
                    api_key,
                    candidates,
                    tier=model["tier"],
                )

            tokens_in = usage.tokens_in if usage is not None else None
            density = None
            if tokens_in is not None and input_chars:
                density = round(tokens_in / (input_chars / 1000), 3)

            row = {
                "date": obs_date,
                "provider_id": model["provider_id"],
                "tier": model["tier"],
                "task_id": task_id,
                "model_id": model["model_id"],
                "api_model": api_model,
                "tokens_in": tokens_in,
                "input_chars": input_chars,
                "tokens_in_per_1k_chars": density,
                "label": TASK_SPECS[task_id]["label"],
                "run_status": status,
                "error": error,
                "corpus_version": CORPUS_VERSION,
                "source": "tokenizer_ledger",
                "run_at": now_iso_z(),
            }
            new_rows.append(row)
            replace.add((obs_date, model["provider_id"], model["tier"], task_id))
            executed_event_groups.add(
                (obs_date, "ledger", model["provider_id"], model["tier"], task_id)
            )
            if status == "ok" and usage is not None:
                input_price, output_price = prices_for_api_model(model, api_model)
                incoming_cost_events.append(
                    build_cost_event(
                        date=obs_date,
                        run_at=row["run_at"],
                        source="ledger",
                        provider_id=model["provider_id"],
                        tier=model["tier"],
                        task_id=task_id,
                        turn=None,
                        request_kind=usage.request_kind,
                        api_model=api_model,
                        input_tokens=usage.tokens_in,
                        output_tokens=usage.tokens_out,
                        input_price_per_1m=input_price,
                        output_price_per_1m=output_price,
                        pricing_snapshot_date=pricing_snapshot_date,
                        corpus_version=CORPUS_VERSION,
                        chat_corpus_version=None,
                        run_id=run_id,
                        billable=usage.billable,
                        replicate=1,
                        attempt=1,
                        canonical=True,
                    )
                )

    keep = [
        row
        for row in existing
        if (row.get("date"), row.get("provider_id"), row.get("tier"), row.get("task_id"))
        not in replace
    ]
    merged = keep + new_rows
    merged.sort(key=lambda r: (r.get("date") or "", r.get("provider_id") or "", r.get("tier") or "", r.get("task_id") or ""))
    # A dry run must not touch the ledger. It calls no provider, so every row it
    # builds carries `run_status: dry_run` — and because rows are replaced by
    # (date, provider, tier, task), writing them *deletes the real counts already
    # collected for today* and substitutes placeholders. Checking which tasks a
    # flag selects is exactly what a dry run is for, and doing it cost a day of
    # collected data before this guard existed.
    if not args.dry_run:
        save_ledger(merged)
        existing_cost_events = load_cost_events()
        keep_cost_events = [
            row
            for row in existing_cost_events
            if (
                row.get("date"),
                row.get("source"),
                row.get("provider_id"),
                row.get("tier"),
                row.get("task_id"),
            )
            not in executed_event_groups
        ]
        merged_cost_events = merge_cost_events(keep_cost_events, incoming_cost_events)
        save_cost_events(merged_cost_events)

    ok = sum(1 for row in new_rows if row["run_status"] == "ok")
    print(
        json.dumps(
            {
                "event": "tokenizer_ledger_written" if not args.dry_run else "tokenizer_ledger_dry_run",
                "date": obs_date,
                "tasks": task_ids,
                "rows_written": len(new_rows),
                "ok_rows": ok,
                "cost_events_written": len(incoming_cost_events),
                "output_file": None if args.dry_run else str(LEDGER_FILE),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
