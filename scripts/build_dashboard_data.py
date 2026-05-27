#!/usr/bin/env python3
"""Build dashboard artifacts from pricing_history/.

Modes:
  --rebuild  walk every pricing_history/*.json (handles 1.x and 2.x schemas),
             regenerate series.json + models.json + index.json from scratch.
  --append   read pricing.json (assumed current schema 2.x) and the date is
             today UTC by default. Appends today's rows to series.json
             (replacing any existing rows for that date), refreshes
             models.json + index.json. O(today) work, suitable for CI.

Artifacts:
  pricing_history/series.json   long-form per-day per-pricing-row token prices
  pricing_history/models.json   per-pricing-row lifecycle (first/last seen, deprecated_on)
  pricing_history/index.json    list of snapshot dates + schema versions

Filter rules: we only keep rows priced per_1M_tokens with service_tier in
{null, "standard"} so the chart compares apples to apples across providers.
Image/video/transcription rows are skipped (different units). The 2.x
`pricing_id` is used as the line key; for 1.x we synthesize one.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = REPO_ROOT / "pricing_history"
LIVE_FILE = REPO_ROOT / "pricing.json"

DASHBOARD_DATA_DIR = REPO_ROOT / "dashboard" / "data"
SERIES_FILE = DASHBOARD_DATA_DIR / "series.json"
MODELS_FILE = DASHBOARD_DATA_DIR / "models.json"
INDEX_FILE = DASHBOARD_DATA_DIR / "index.json"


# ---- normalization ---------------------------------------------------------

def _synth_pricing_id_v1(row: dict) -> str:
    return f"{row['provider_id']}-{row['model_id']}-{row.get('type','chat')}"


def normalize_snapshot(snapshot: dict, date: str) -> list[dict]:
    """Flatten one pricing.json snapshot into chart-ready rows for `date`."""
    schema = snapshot.get("meta", {}).get("schema_version", "")
    rows: list[dict] = []

    for r in snapshot.get("pricing", []):
        if schema.startswith("1."):
            # 1.x: only keep chat rows priced per_1M_tokens
            if r.get("type") != "chat":
                continue
            if r.get("unit") != "per_1M_tokens":
                continue
            rows.append({
                "date": date,
                "pricing_id": _synth_pricing_id_v1(r),
                "provider_id": r["provider_id"],
                "model_id": r["model_id"],
                "display_name": r.get("display_name") or r["model_id"],
                "service_tier": "standard",
                "category": "standard_api",
                "input_price": r.get("input_price"),
                "output_price": r.get("output_price"),
                "cached_input_price": None,
                "currency": r.get("currency", "USD"),
                "is_active": r.get("is_active", True),
            })
        else:
            # 2.x
            if r.get("input_unit") != "per_1M_tokens":
                continue
            tier = r.get("service_tier")
            if tier not in (None, "standard"):
                continue
            rows.append({
                "date": date,
                "pricing_id": r["pricing_id"],
                "provider_id": r["provider_id"],
                "model_id": r["model_id"],
                "display_name": r.get("display_name") or r["model_id"],
                "service_tier": tier or "standard",
                "category": r.get("category"),
                "input_price": r.get("input_price"),
                "output_price": r.get("output_price"),
                "cached_input_price": r.get("cache_read_price"),
                "currency": r.get("currency", "USD"),
                "is_active": r.get("is_active", True),
            })
    return rows


# ---- aggregation -----------------------------------------------------------

DEPRECATED_HINTS = ("deprecated", "retired", "legacy")


def _name_marks_deprecation(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in DEPRECATED_HINTS)


def build_models(series: list[dict], schema_by_date: dict[str, str]) -> list[dict]:
    """Per pricing_id lifecycle summary for the archive view.

    pricing_ids that only ever appeared under schema 1.x are dropped: the
    scraper rewrote model_ids when it moved to 2.x, so those entries are
    rename artifacts, not real model deprecations.
    """
    def is_legacy_schema(date: str) -> bool:
        return schema_by_date.get(date, "").startswith("1.")

    by_id: dict[str, list[dict]] = {}
    for row in series:
        by_id.setdefault(row["pricing_id"], []).append(row)

    latest_date = max((r["date"] for r in series), default=None)

    models: list[dict] = []
    for pid, rows in by_id.items():
        rows.sort(key=lambda r: r["date"])

        # Drop pricing_ids whose most recent appearance was under schema 1.x —
        # they're scraper-rewrite ghosts, not deprecations the user cares about.
        if is_legacy_schema(rows[-1]["date"]):
            continue

        first = rows[0]
        last = rows[-1]
        active_dates = [r["date"] for r in rows if r["is_active"]]
        inactive_dates = [r["date"] for r in rows if not r["is_active"]]

        last_active = max(active_dates) if active_dates else None
        # first date where the model appears flagged inactive after last_active
        deprecated_on = None
        if last_active:
            later_inactive = [d for d in inactive_dates if d > last_active]
            deprecated_on = min(later_inactive) if later_inactive else None
        elif inactive_dates:
            deprecated_on = min(inactive_dates)

        currently_present = (last["date"] == latest_date)
        currently_active = currently_present and last["is_active"]

        # disappeared = was present in some snapshot but missing from latest
        disappeared_after = None if currently_present else last["date"]

        models.append({
            "pricing_id": pid,
            "provider_id": last["provider_id"],
            "model_id": last["model_id"],
            "display_name": last["display_name"],
            "category": last.get("category"),
            "first_seen": first["date"],
            "last_seen": last["date"],
            "last_active": last_active,
            "deprecated_on": deprecated_on,
            "disappeared_after": disappeared_after,
            "currently_present": currently_present,
            "currently_active": currently_active,
            "name_marks_deprecation": _name_marks_deprecation(last["display_name"]),
            "latest_input": last.get("input_price"),
            "latest_output": last.get("output_price"),
            "latest_cached_input": last.get("cached_input_price"),
            "currency": last.get("currency", "USD"),
        })

    models.sort(key=lambda m: (m["provider_id"], m["model_id"]))
    return models


def build_index(series: list[dict], schema_by_date: dict[str, str]) -> dict:
    dates = sorted({r["date"] for r in series})
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dates": dates,
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "snapshot_count": len(dates),
        "row_count": len(series),
        "schema_versions": schema_by_date,
    }


# ---- modes -----------------------------------------------------------------

def cmd_rebuild() -> int:
    if not HISTORY_DIR.exists():
        print(f"error: {HISTORY_DIR} does not exist. Run backfill_pricing_history.py first.", file=sys.stderr)
        return 1
    DASHBOARD_DATA_DIR.mkdir(parents=True, exist_ok=True)

    series: list[dict] = []
    schema_by_date: dict[str, str] = {}

    snapshot_files = sorted(p for p in HISTORY_DIR.glob("*.json"))
    if not snapshot_files:
        print(f"error: no snapshots in {HISTORY_DIR}", file=sys.stderr)
        return 1

    for path in snapshot_files:
        date = path.stem  # YYYY-MM-DD
        try:
            snap = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"  skip {path.name}: {e}", file=sys.stderr)
            continue
        schema_by_date[date] = snap.get("meta", {}).get("schema_version", "")
        rows = normalize_snapshot(snap, date)
        series.extend(rows)
        print(f"  {date}: {len(rows)} rows (schema {schema_by_date[date]})")

    models = build_models(series, schema_by_date)
    index = build_index(series, schema_by_date)

    SERIES_FILE.write_text(json.dumps(series) + "\n")
    MODELS_FILE.write_text(json.dumps(models, indent=2) + "\n")
    INDEX_FILE.write_text(json.dumps(index, indent=2) + "\n")

    print(f"\nWrote {SERIES_FILE.name} ({len(series)} rows)")
    print(f"Wrote {MODELS_FILE.name} ({len(models)} pricing_ids)")
    print(f"Wrote {INDEX_FILE.name} ({index['snapshot_count']} dates)")
    return 0


def cmd_append(date: str | None) -> int:
    if not LIVE_FILE.exists():
        print(f"error: {LIVE_FILE} not found", file=sys.stderr)
        return 1
    DASHBOARD_DATA_DIR.mkdir(parents=True, exist_ok=True)

    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    snap = json.loads(LIVE_FILE.read_text())
    new_rows = normalize_snapshot(snap, date)

    if SERIES_FILE.exists():
        series = json.loads(SERIES_FILE.read_text())
    else:
        series = []

    # replace any existing rows for `date` (idempotent re-runs)
    series = [r for r in series if r["date"] != date]
    series.extend(new_rows)
    series.sort(key=lambda r: (r["date"], r["provider_id"], r["pricing_id"]))

    if INDEX_FILE.exists():
        prior_index = json.loads(INDEX_FILE.read_text())
        schema_by_date = prior_index.get("schema_versions", {})
    else:
        schema_by_date = {}
    schema_by_date[date] = snap.get("meta", {}).get("schema_version", "")

    models = build_models(series, schema_by_date)
    index = build_index(series, schema_by_date)

    SERIES_FILE.write_text(json.dumps(series) + "\n")
    MODELS_FILE.write_text(json.dumps(models, indent=2) + "\n")
    INDEX_FILE.write_text(json.dumps(index, indent=2) + "\n")

    print(f"Appended {len(new_rows)} rows for {date} (total {len(series)} rows, {len(models)} pricing_ids)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build dashboard artifacts from pricing history.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--rebuild", action="store_true", help="rebuild artifacts from all pricing_history/*.json")
    g.add_argument("--append", action="store_true", help="append today's pricing.json into existing artifacts")
    ap.add_argument("--date", help="override the date (YYYY-MM-DD) for --append; defaults to today UTC")
    args = ap.parse_args()

    if args.rebuild:
        return cmd_rebuild()
    return cmd_append(args.date)


if __name__ == "__main__":
    sys.exit(main())
