"""Post-remediation verification gates for colonial pricing pipeline."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scrape_pricing as pricing

PRICING_JSON = Path("pricing.json")
RUN_REPORT = Path("run_report.json")
PRICING_HISTORY = Path("pricing_history")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def verify_pricing_run() -> dict:
    checks: list[dict] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    if not PRICING_JSON.exists():
        add("pricing_json_exists", False, "pricing.json missing")
        return {"passed": False, "checks": checks}

    try:
        doc = json.loads(PRICING_JSON.read_text())
    except json.JSONDecodeError as exc:
        add("pricing_json_parse", False, str(exc))
        return {"passed": False, "checks": checks}

    rows = doc.get("pricing", [])
    schema_version = doc.get("meta", {}).get("schema_version")
    add("schema_version", schema_version == "2.1.0", f"schema_version={schema_version}")

    add(
        "total_row_floor",
        len(rows) >= pricing.MIN_TOTAL_TIERS,
        f"rows={len(rows)} floor={pricing.MIN_TOTAL_TIERS}",
    )

    counts = pricing.provider_counts(rows)
    for provider, floor in pricing.MIN_PROVIDER_TIERS.items():
        current = counts.get(provider, 0)
        add(
            f"provider_floor_{provider}",
            current >= floor,
            f"{provider}={current} floor={floor}",
        )

    history_today = PRICING_HISTORY / f"{_today_str()}.json"
    add(
        "history_freshness",
        history_today.exists(),
        f"expected {history_today}",
    )

    if RUN_REPORT.exists():
        try:
            report = json.loads(RUN_REPORT.read_text())
            status = report.get("status")
            add(
                "run_report_status",
                status in ("success", "degraded"),
                f"status={status}",
            )
        except json.JSONDecodeError as exc:
            add("run_report_parse", False, str(exc))
    else:
        add("run_report_exists", False, "run_report.json missing")

    passed = all(c["passed"] for c in checks)
    return {"passed": passed, "checks": checks}


def main() -> int:
    result = verify_pricing_run()
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
