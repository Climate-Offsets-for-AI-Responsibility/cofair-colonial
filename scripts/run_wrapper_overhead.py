#!/usr/bin/env python3
"""Weekly Test 4 — chat wrapper overhead on a frozen 10-turn transcript.

For each pinned model, count prompt tokens on cumulative prefixes at turns
1..10. Never regenerates assistant text.

Writes dashboard/data/wrapper_runs.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provider_token_count import (  # noqa: E402
    api_model_candidates,
    count_prompt_tokens_messages,
    env_for_provider,
)
from task_corpus import (  # noqa: E402
    CHAT_CORPUS_VERSION,
    transcript_prefix,
    transcript_prefix_chars,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SHARED_ENV = REPO_ROOT.parent / "cofair" / ".env" / ".env.cofair"
load_dotenv()
if DEFAULT_SHARED_ENV.exists():
    load_dotenv(DEFAULT_SHARED_ENV, override=False)

DATA_DIR = REPO_ROOT / "dashboard" / "data"
EQUIVALENCE_FILE = DATA_DIR / "equivalence.json"
WRAPPER_FILE = DATA_DIR / "wrapper_runs.json"


def now_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def current_week_anchor(today: date | None = None) -> str:
    d = today or datetime.now(timezone.utc).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def load_panel(mode: str = "two") -> list[dict]:
    eq = json.loads(EQUIVALENCE_FILE.read_text())
    return list(eq["selected_models_by_mode"][mode])


def load_rows() -> list[dict]:
    if not WRAPPER_FILE.exists():
        return []
    return json.loads(WRAPPER_FILE.read_text()).get("rows", [])


def save_rows(rows: list[dict]) -> None:
    WRAPPER_FILE.write_text(
        json.dumps(
            {
                "generated_at": now_iso_z(),
                "chat_corpus_version": CHAT_CORPUS_VERSION,
                "row_count": len(rows),
                "rows": rows,
            },
            indent=2,
        )
        + "\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Test 4 wrapper overhead counts.")
    ap.add_argument("--week", help="Override week anchor YYYY-MM-DD")
    ap.add_argument("--mode", choices=["two", "three"], default="two")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--provider", help="Limit to one provider_id")
    ap.add_argument("--max-turn", type=int, default=10, help="Highest turn to count (1-10)")
    args = ap.parse_args()

    run_week = args.week or current_week_anchor()
    max_turn = max(1, min(10, args.max_turn))
    models = load_panel(args.mode)
    if args.provider:
        models = [m for m in models if m["provider_id"] == args.provider]

    existing = load_rows()
    replace = set()
    new_rows: list[dict] = []

    for model in models:
        api_key = env_for_provider(model["provider_id"])
        candidates = api_model_candidates(
            model["provider_id"],
            model["model_id"],
            model["tier"],
            api_key,
            model.get("api_candidates"),
        )
        api_model = candidates[0] if candidates else model["model_id"]
        for turn in range(1, max_turn + 1):
            messages = transcript_prefix(turn)
            prefix_chars = transcript_prefix_chars(turn)

            if args.dry_run:
                status, tokens, error = "dry_run", None, None
            elif not api_key:
                status, tokens, error = "missing_key", None, "provider API key missing"
            else:
                status, tokens, error, api_model = count_prompt_tokens_messages(
                    model["provider_id"], api_model, messages, api_key, candidates
                )

            row = {
                "run_week": run_week,
                "provider_id": model["provider_id"],
                "tier": model["tier"],
                "turn": turn,
                "model_id": model["model_id"],
                "api_model": api_model,
                "api_prompt_tokens": tokens,
                "prefix_chars": prefix_chars,
                "tokens_per_1k_chars": (
                    round(tokens / (prefix_chars / 1000), 3)
                    if tokens is not None and prefix_chars
                    else None
                ),
                "run_status": status,
                "error": error,
                "chat_corpus_version": CHAT_CORPUS_VERSION,
                "run_at": now_iso_z(),
            }
            new_rows.append(row)
            replace.add((run_week, model["provider_id"], model["tier"], turn))

    keep = [
        row
        for row in existing
        if (row.get("run_week"), row.get("provider_id"), row.get("tier"), row.get("turn"))
        not in replace
    ]
    merged = keep + new_rows
    merged.sort(
        key=lambda r: (
            r.get("run_week") or "",
            r.get("provider_id") or "",
            r.get("tier") or "",
            r.get("turn") or 0,
        )
    )
    save_rows(merged)

    ok = sum(1 for row in new_rows if row["run_status"] == "ok")
    print(
        json.dumps(
            {
                "event": "wrapper_runs_written",
                "run_week": run_week,
                "max_turn": max_turn,
                "rows_written": len(new_rows),
                "ok_rows": ok,
                "output_file": str(WRAPPER_FILE),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
