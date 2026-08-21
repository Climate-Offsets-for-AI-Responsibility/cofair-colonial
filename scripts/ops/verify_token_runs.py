#!/usr/bin/env python3
"""Post-run gate for the token-equivalence pipelines.

Why this exists: the weekly/daily token workflows record per-provider failures
as rows and then exit 0, so a provider whose every call failed looked identical
to a healthy one — the job reported success, Slack said success, and `/tokens`
simply drew fewer lines. This reads the rebuilt `equivalence.json` and fails
when a panel row collected nothing from a source that was supposed to run.

Usage: verify_token_runs.py --sources ledger [--sources ...] [--json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EQUIVALENCE_FILE = REPO_ROOT / "dashboard" / "data" / "equivalence.json"

SOURCES = ("meter", "ledger", "wrapper")


def verify_token_runs(sources: list[str], path: Path = EQUIVALENCE_FILE) -> dict:
    checks: list[dict] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    if not path.exists():
        add("equivalence_exists", False, f"{path} missing")
        return {"passed": False, "checks": checks}

    payload = json.loads(path.read_text())
    health = (payload.get("provider_health") or {}).get("panel")
    if not health:
        add(
            "provider_health_present",
            False,
            "equivalence.json has no provider_health — rebuild with build_dashboard_data.py",
        )
        return {"passed": False, "checks": checks}

    add("provider_health_present", True, f"{len(health)} panel rows")

    for source in sources:
        dark = []
        silent = []
        for item in health:
            stats = (item.get("sources") or {}).get(source) or {}
            label = f"{item['provider_id']}·{item['tier']}"
            if stats.get("ok_count"):
                continue
            if stats.get("error_count"):
                dark.append(f"{label} ({stats.get('last_error_model')}): {stats.get('last_error')}")
            else:
                silent.append(label)
        add(
            f"{source}_no_dark_providers",
            not dark,
            "; ".join(dark) if dark else "every panel row reported at least once",
        )
        add(
            f"{source}_no_silent_providers",
            not silent,
            ", ".join(silent) if silent else "every panel row was attempted",
        )

    return {"passed": all(check["passed"] for check in checks), "checks": checks}


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify token-equivalence collection health.")
    ap.add_argument(
        "--sources",
        action="append",
        choices=SOURCES,
        help="Source to gate on; repeatable. Defaults to all three.",
    )
    args = ap.parse_args()

    result = verify_token_runs(args.sources or list(SOURCES))
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        failed = [check["name"] for check in result["checks"] if not check["passed"]]
        print(f"::error::token run verification failed: {', '.join(failed)}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
