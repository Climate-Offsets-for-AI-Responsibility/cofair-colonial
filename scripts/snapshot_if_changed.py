#!/usr/bin/env python3
"""Copy pricing.json to pricing_history/<UTC-today>.json only if its content
has actually changed since the latest snapshot.

The pricing.json `meta.last_run_datetime` field changes every run, so a naive
file diff would always claim a change. We compare just the `providers` and
`pricing` arrays. Idempotent: re-running on the same day is a no-op when
nothing meaningful changed.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "pricing.json"
DIR = REPO_ROOT / "pricing_history"


def fingerprint(d: dict) -> tuple:
    return (d.get("providers"), d.get("pricing"))


def main() -> int:
    if not SRC.exists():
        print(f"error: {SRC} not found", file=sys.stderr)
        return 1

    DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest = DIR / f"{today}.json"

    src_data = json.loads(SRC.read_text())
    snaps = sorted(DIR.glob("*.json"))

    if snaps:
        latest_path = snaps[-1]
        try:
            latest_data = json.loads(latest_path.read_text())
        except json.JSONDecodeError:
            latest_data = None
        if latest_data and fingerprint(latest_data) == fingerprint(src_data):
            print(f"pricing unchanged from {latest_path.name}; skipping snapshot")
            return 0

    dest.write_text(SRC.read_text())
    print(f"wrote {dest.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
