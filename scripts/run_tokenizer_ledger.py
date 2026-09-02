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
from task_corpus import CORPUS_VERSION, TASK_PROMPTS, TASK_SPECS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SHARED_ENV = REPO_ROOT.parent / "cofair" / ".env" / ".env.cofair"
load_dotenv()
if DEFAULT_SHARED_ENV.exists():
    load_dotenv(DEFAULT_SHARED_ENV, override=False)

DATA_DIR = REPO_ROOT / "dashboard" / "data"
EQUIVALENCE_FILE = DATA_DIR / "equivalence.json"
LEDGER_FILE = DATA_DIR / "tokenizer_ledger.json"

TASK_SETS = {
    "ABC": ["A", "B", "C"],
    "D": ["D"],
    "ABCD": ["A", "B", "C", "D"],
}


def now_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_panel(mode: str = "two") -> list[dict]:
    eq = json.loads(EQUIVALENCE_FILE.read_text())
    return list(eq["selected_models_by_mode"][mode])


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
    models = load_panel(args.mode)
    if args.provider:
        models = [m for m in models if m["provider_id"] == args.provider]

    existing = load_ledger()
    replace = set()
    new_rows: list[dict] = []

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

            if args.dry_run:
                status, tokens_in, error = "dry_run", None, None
            elif not api_key:
                status, tokens_in, error = "missing_key", None, "provider API key missing"
            else:
                status, tokens_in, error, api_model = count_prompt_tokens_text(
                    model["provider_id"],
                    api_model,
                    prompt,
                    api_key,
                    candidates,
                    tier=model["tier"],
                )

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

    keep = [
        row
        for row in existing
        if (row.get("date"), row.get("provider_id"), row.get("tier"), row.get("task_id"))
        not in replace
    ]
    merged = keep + new_rows
    merged.sort(key=lambda r: (r.get("date") or "", r.get("provider_id") or "", r.get("tier") or "", r.get("task_id") or ""))
    save_ledger(merged)

    ok = sum(1 for row in new_rows if row["run_status"] == "ok")
    print(
        json.dumps(
            {
                "event": "tokenizer_ledger_written",
                "date": obs_date,
                "tasks": task_ids,
                "rows_written": len(new_rows),
                "ok_rows": ok,
                "output_file": str(LEDGER_FILE),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
