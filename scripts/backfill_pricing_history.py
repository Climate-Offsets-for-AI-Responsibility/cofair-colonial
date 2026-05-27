#!/usr/bin/env python3
"""Backfill pricing_history/ from git history of pricing.json.

Walks every commit that touched pricing.json and writes
pricing_history/YYYY-MM-DD.json using the commit's UTC date. When a date
has multiple commits, the most recent one wins (we walk newest-first and
skip dates we've already written).
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE = "pricing.json"
OUT_DIR = REPO_ROOT / "pricing_history"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO_ROOT, text=True)


def iter_commits():
    """Yield (sha, commit_date_utc) newest-first for commits touching pricing.json."""
    out = git("log", "--format=%H\t%cI", "--", TARGET_FILE)
    for line in out.splitlines():
        sha, iso = line.split("\t", 1)
        dt = datetime.fromisoformat(iso).astimezone(timezone.utc)
        yield sha, dt


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0
    seen_dates: set[str] = set()

    for sha, dt in iter_commits():
        date_key = dt.strftime("%Y-%m-%d")
        if date_key in seen_dates:
            skipped += 1
            continue
        seen_dates.add(date_key)

        try:
            raw = git("show", f"{sha}:{TARGET_FILE}")
        except subprocess.CalledProcessError:
            print(f"  skip {sha[:8]} {date_key}: file missing at this commit", file=sys.stderr)
            continue

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  skip {sha[:8]} {date_key}: invalid JSON ({e})", file=sys.stderr)
            continue

        dest = OUT_DIR / f"{date_key}.json"
        dest.write_text(json.dumps(parsed, indent=2) + "\n")
        written += 1
        print(f"  wrote {dest.name} from {sha[:8]}")

    print(f"\nBackfill complete: {written} files written, {skipped} same-day duplicates skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
