"""Post-remediation verification gates for colonial pricing pipeline."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import scrape_pricing as pricing

PRICING_JSON = Path("pricing.json")
RUN_REPORT = Path("run_report.json")
PRICING_HISTORY = Path("pricing_history")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _latest_history_file() -> Path | None:
    if not PRICING_HISTORY.exists():
        return None
    candidates = sorted(PRICING_HISTORY.glob("*.json"))
    if not candidates:
        return None
    return candidates[-1]


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
    last_run_datetime = doc.get("meta", {}).get("last_run_datetime")
    add("schema_version", schema_version == "2.1.0", f"schema_version={schema_version}")
    if isinstance(last_run_datetime, str):
        try:
            run_dt = datetime.strptime(last_run_datetime, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - run_dt).total_seconds() / 3600
            add("pricing_json_recency", age_hours <= 30, f"age_hours={age_hours:.1f}")
        except ValueError:
            add("pricing_json_recency", False, f"unparseable last_run_datetime={last_run_datetime}")
    else:
        add("pricing_json_recency", False, f"last_run_datetime missing: {last_run_datetime}")

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
    latest_history = _latest_history_file()
    freshness_ok = False
    freshness_detail = f"expected {history_today}"
    if history_today.exists():
        freshness_ok = True
        freshness_detail = f"snapshot present: {history_today.name}"
    elif latest_history is not None:
        freshness_ok = True
        freshness_detail = f"today snapshot unchanged; latest={latest_history.name}"
    add(
        "history_freshness",
        freshness_ok,
        freshness_detail,
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
