#!/usr/bin/env python3
"""Copy pricing.json to pricing_history/<UTC-today>.json.

Every scrape day gets a dated file, even when list prices did not move. The
dashboard is built only from these files, so skipping an unchanged day freezes
the public chart at the last *change*. `meta.last_run_datetime` is ignored in
the fingerprint so a same-day re-run is still a no-op.
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


def snapshot_pricing(src: Path, dest_dir: Path, today: str) -> str:
    """Write dest_dir/<today>.json when this calendar day has no matching snapshot.

    A new day is always written. Skip only when today's file already exists and
    its providers/pricing fingerprint matches the live scrape.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{today}.json"
    src_data = json.loads(src.read_text())

    if dest.exists():
        try:
            existing = json.loads(dest.read_text())
        except json.JSONDecodeError:
            existing = None
        if existing and fingerprint(existing) == fingerprint(src_data):
            return f"pricing unchanged from {dest.name}; skipping snapshot"

    dest.write_text(src.read_text())
    return f"wrote {dest.name}"


def main() -> int:
    if not SRC.exists():
        print(f"error: {SRC} not found", file=sys.stderr)
        return 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(snapshot_pricing(SRC, DIR, today))
    return 0


if __name__ == "__main__":
    sys.exit(main())
