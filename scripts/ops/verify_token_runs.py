#!/usr/bin/env python3
"""Post-run gate for the token-equivalence pipelines.

Why this exists: the weekly/daily token workflows record per-provider failures
as rows and then exit 0, so a provider whose every call failed looked identical
to a healthy one — the job reported success, Slack said success, and `/tokens`
simply drew fewer lines. This reads the rebuilt `equivalence.json` and fails
when a panel row collected nothing from a source that was supposed to run.

Account-level faults (billing, revoked keys) are quarantined: the job exits 0 as
`degraded`, writes `ops/token_run_report.json`, and surfaces an action-required
alert instead of a daily red X.

Usage: verify_token_runs.py --sources ledger [--sources ...] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EQUIVALENCE_FILE = REPO_ROOT / "dashboard" / "data" / "equivalence.json"
TOKEN_RUN_REPORT = REPO_ROOT / "ops" / "token_run_report.json"

SOURCES = ("meter", "ledger", "wrapper")

sys.path.insert(0, str(REPO_ROOT / "scripts" / "ops"))
from provider_faults import remedy_for_error  # noqa: E402


def _tiers_sharing_a_model(health: list[dict], source: str) -> list[str]:
    """Providers whose flagship and workhorse rows were counted on one model.

    The failure this catches is silent by construction: both rows report ok, and
    the two tiers publish identical token counts. Four providers in the panel
    genuinely do share a tokenizer across their families, so identical numbers
    are not themselves suspicious — which is why google flagship sat on
    `gemini-flash-latest` for the whole ledger without anything looking wrong
    (D77). The model id is the part that cannot be a coincidence.
    """
    served: dict[str, dict[str, set[str]]] = {}
    for item in health:
        models = ((item.get("sources") or {}).get(source) or {}).get("ok_models") or []
        if models:
            served.setdefault(item["provider_id"], {})[item["tier"]] = set(models)

    collapsed = []
    for provider_id, tiers in sorted(served.items()):
        if len(tiers) < 2:
            continue
        shared = set.intersection(*tiers.values())
        if shared:
            collapsed.append(
                f"{provider_id}: {', '.join(sorted(tiers))} all counted on {', '.join(sorted(shared))}"
            )
    return collapsed


def verify_token_runs(sources: list[str], path: Path = EQUIVALENCE_FILE) -> dict:
    checks: list[dict] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    if not path.exists():
        add("equivalence_exists", False, f"{path} missing")
        return {"passed": False, "status": "failed", "checks": checks, "unavailable": []}

    payload = json.loads(path.read_text())
    health = (payload.get("provider_health") or {}).get("panel")
    if not health:
        add(
            "provider_health_present",
            False,
            "equivalence.json has no provider_health — rebuild with build_dashboard_data.py",
        )
        return {"passed": False, "status": "failed", "checks": checks, "unavailable": []}

    add("provider_health_present", True, f"{len(health)} panel rows")

    unavailable_entries: list[dict] = []

    for source in sources:
        dark = []
        silent = []
        unavailable = []
        for item in health:
            stats = (item.get("sources") or {}).get(source) or {}
            label = f"{item['provider_id']}·{item['tier']}"
            if stats.get("ok_count"):
                continue
            if stats.get("error_count"):
                dark.append(f"{label} ({stats.get('last_error_model')}): {stats.get('last_error')}")
            elif stats.get("unavailable_count"):
                remedy = remedy_for_error(stats.get("last_error") or "", item["provider_id"])
                unavailable.append(f"{label}: {remedy}")
                unavailable_entries.append(
                    {
                        "provider_id": item["provider_id"],
                        "tier": item["tier"],
                        "source": source,
                        "signature": "ProviderAccountFault",
                        "remedy": remedy,
                        "error": stats.get("last_error"),
                    }
                )
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
        add(
            f"{source}_account_faults_quarantined",
            True,
            "; ".join(unavailable) if unavailable else "no account-level provider faults",
        )

        collapsed = _tiers_sharing_a_model(health, source)
        add(
            f"{source}_tiers_resolve_distinct_models",
            not collapsed,
            "; ".join(collapsed) if collapsed else "each tier resolved to its own model",
        )

    hard_failed = any(
        not check["passed"]
        for check in checks
        if check["name"].endswith("_no_dark_providers")
        or check["name"].endswith("_no_silent_providers")
        or check["name"].endswith("_tiers_resolve_distinct_models")
    )
    has_unavailable = bool(unavailable_entries)
    if hard_failed:
        status = "failed"
        passed = False
    elif has_unavailable:
        status = "degraded"
        passed = True
    else:
        status = "success"
        passed = True

    return {
        "passed": passed,
        "status": status,
        "checks": checks,
        "unavailable": unavailable_entries,
    }


def _retry_targets_from_checks(checks: list[dict]) -> list[dict]:
    targets: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for check in checks:
        if check.get("passed", True) or not check["name"].endswith("_no_dark_providers"):
            continue
        source = check["name"].replace("_no_dark_providers", "")
        for part in (check.get("detail") or "").split(";"):
            part = part.strip()
            if not part or part.startswith("every panel"):
                continue
            match = re.match(r"([^·]+)·([^\s(]+)", part)
            if not match:
                continue
            key = (match.group(1), source)
            if key in seen:
                continue
            seen.add(key)
            targets.append({"provider_id": match.group(1), "source": source})
    return targets


def write_token_run_report(result: dict, run_id: str | None = None) -> Path:
    TOKEN_RUN_REPORT.parent.mkdir(parents=True, exist_ok=True)
    unavailable = result.get("unavailable") or []
    signatures = sorted({entry.get("signature") for entry in unavailable if entry.get("signature")})
    primary_signature = signatures[0] if len(signatures) == 1 else "ProviderAccountFault"
    report = {
        "status": result.get("status", "failed"),
        "passed": result.get("passed", False),
        "signature": primary_signature if result.get("status") == "degraded" else "TransientProviderFault",
        "unavailable": unavailable,
        "checks": result.get("checks", []),
        "run_id": run_id,
    }
    if result.get("status") == "failed":
        failed = [
            check["name"]
            for check in result.get("checks", [])
            if not check["passed"]
            and (
                check["name"].endswith("_no_dark_providers")
                or check["name"].endswith("_no_silent_providers")
            )
        ]
        report["signature"] = "TransientProviderFault" if failed else "UnknownError"
        report["error"] = ", ".join(failed)
        report["retry_targets"] = _retry_targets_from_checks(result.get("checks", []))
    elif result.get("status") == "degraded":
        remedies = [entry.get("remedy") for entry in unavailable if entry.get("remedy")]
        report["error"] = "; ".join(dict.fromkeys(remedies)) if remedies else "provider account fault"
    TOKEN_RUN_REPORT.write_text(json.dumps(report, indent=2) + "\n")
    return TOKEN_RUN_REPORT


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify token-equivalence collection health.")
    ap.add_argument(
        "--sources",
        action="append",
        choices=SOURCES,
        help="Source to gate on; repeatable. Defaults to all three.",
    )
    ap.add_argument("--json", action="store_true", help="Print full result JSON only.")
    ap.add_argument("--run-id", default=None, help="Workflow run id for the token run report.")
    args = ap.parse_args()

    result = verify_token_runs(args.sources or list(SOURCES))
    if result["status"] in ("degraded", "failed"):
        write_token_run_report(result, run_id=args.run_id)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))

    if not result["passed"]:
        failed = [check["name"] for check in result["checks"] if not check["passed"]]
        print(f"::error::token run verification failed: {', '.join(failed)}")
        return 1

    if result["status"] == "degraded":
        unavailable = result.get("unavailable") or []
        summary = "; ".join(
            f"{entry['provider_id']}·{entry['tier']} ({entry['source']})" for entry in unavailable
        )
        print(f"::warning::token run verification degraded (action required): {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
