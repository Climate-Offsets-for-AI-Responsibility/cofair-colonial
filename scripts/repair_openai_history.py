#!/usr/bin/env python3
"""Repair OpenAI flagship rows scraped before the pricing.md parser (D131).

From 2026-09-03 through 2026-10-16 the HTML shell did two things to the
flagship card:

- It server-rendered only the first rows of each table, so a model pushed
  below that fold (gpt-5.6 Luna after GPT-6 landed on Sep 22) disappeared
  for weeks and the chart drew a straight line between the last stored
  price and the next one.
- It had gained a cache-writes column. Fixed indexes stored that rate, or
  the neighboring one, as output. GPT-6 Astra's long-context output was
  recorded as $2; the card said $75. Luna's long-context output was
  recorded as $0.04 (the cached-input rate); the card said $1.80.

The Oct 17 scrape reads pricing.md and maps columns by header. This repair
rewrites only standard-tier flagship rows on the earlier days, and only
when every trusted reading of that row — a reparse of that day's archived
page, or the post-fix snapshot — agrees on the price. A model that was not
on the card yet is not backdated. A model the corrected card introduced on
Oct 17 is not projected backward.

Snapshots from 2026-10-17 on are left alone.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = REPO_ROOT / "pricing_history"
REPAIR_START = "2026-09-03"
REPAIR_END = "2026-10-16"
TRUSTED_FROM = "2026-10-17"
PAGE_URL = "https://developers.openai.com/api/docs/pricing"
PRICE_FIELDS = ("input", "cached_input", "output")

# Rates the published card held across every archive we checked
# (2026-09-12, 09-15, 09-29, 10-02, 10-03, 10-10) and the corrected scrape.
# Writing aborts if the plan does not reproduce them on every repaired day
# the row should occupy. Astra is on the card for the whole window. Luna is
# too; the missing days are the truncation, not a delisting.
EXPECTED_OUTPUT = {
    "openai-gpt-6-astra-standard-shortcontext-flagship": 50.0,
    "openai-gpt-6-astra-standard-longcontext-flagship": 75.0,
    "openai-gpt-5.6-luna-standard-shortcontext-flagship": 1.2,
    "openai-gpt-5.6-luna-standard-longcontext-flagship": 1.8,
}


def is_standard_flagship(row: dict) -> bool:
    return (
        row.get("provider_id") == "openai"
        and row.get("service_tier") == "standard"
        and row.get("category") == "flagship"
        and bool(row.get("pricing_id"))
    )


def index_flagship(rows: list[dict] | None) -> dict[str, dict]:
    found = {}
    for row in rows or []:
        if is_standard_flagship(row):
            found[row["pricing_id"]] = row
    return found


def price_tuple(row: dict) -> tuple:
    def num(value):
        if value is None:
            return None
        return round(float(value), 6)

    return tuple(num(row.get(f"{field}_price")) for field in PRICE_FIELDS)


def write_prices(dest: dict, src: dict) -> None:
    for field in PRICE_FIELDS:
        dest[f"{field}_price"] = src.get(f"{field}_price")
        unit = src.get(f"{field}_unit")
        if unit:
            dest[f"{field}_unit"] = unit


def repair_snapshots(
    snapshot_by_day: dict[str, list[dict]],
    reparsed_by_day: dict[str, list[dict]],
    trusted_by_day: dict[str, list[dict]],
) -> tuple[dict[str, list[dict]], list[tuple]]:
    """Return rewritten pricing lists for days that change, plus a change log.

    ``snapshot_by_day`` is the pre-fix window and is the only set rewritten.
    ``reparsed_by_day`` is a reparse of that day's archived pricing page.
    ``trusted_by_day`` is the post-fix snapshots. Both of the latter are
    trusted readings. A price is filled across a hole only when every trusted
    reading of that pricing id agrees, and only between the first and last
    day the id was actually seen.
    """
    trusted_obs: dict[str, list[tuple[str, dict]]] = {}
    for source in (reparsed_by_day, trusted_by_day):
        for day, rows in source.items():
            for pricing_id, row in index_flagship(rows).items():
                trusted_obs.setdefault(pricing_id, []).append((day, row))

    stable: dict[str, dict] = {}
    for pricing_id, samples in trusted_obs.items():
        if not any(day <= REPAIR_END for day, _row in samples):
            continue
        if len({price_tuple(row) for _day, row in samples}) != 1:
            continue
        stable[pricing_id] = samples[0][1]

    presence: dict[str, set[str]] = {}
    for source in (snapshot_by_day, reparsed_by_day, trusted_by_day):
        for day, rows in source.items():
            for pricing_id in index_flagship(rows):
                presence.setdefault(pricing_id, set()).add(day)

    # Restored rows have to be a series the pre-fix scrape already knew.
    # A full-card archive also lists models the HTML shell never rendered;
    # those are not written back onto the earlier days.
    known_ids: set[str] = set()
    for rows in snapshot_by_day.values():
        known_ids.update(index_flagship(rows))

    templates: dict[str, dict] = {}
    for day in sorted(snapshot_by_day):
        for pricing_id, row in index_flagship(snapshot_by_day[day]).items():
            templates[pricing_id] = row
    for day in sorted(trusted_by_day):
        for pricing_id, row in index_flagship(trusted_by_day[day]).items():
            templates[pricing_id] = row

    rewritten: dict[str, list[dict]] = {}
    changes: list[tuple] = []
    for day in sorted(snapshot_by_day):
        rows = [dict(row) for row in snapshot_by_day[day]]
        current = index_flagship(rows)
        direct = index_flagship(reparsed_by_day.get(day, []))
        day_changed = False

        for pricing_id, row in current.items():
            source = _source_for(pricing_id, direct, stable, presence, day)
            if source is None or price_tuple(row) == price_tuple(source):
                continue
            write_prices(row, source)
            day_changed = True
            changes.append(("update", day, pricing_id, price_tuple(source)))

        inserts = []
        for pricing_id in sorted(set(direct) | set(stable)):
            if pricing_id in current or pricing_id not in known_ids:
                continue
            if not _inside(pricing_id, presence, day):
                continue
            source = direct.get(pricing_id) or stable.get(pricing_id)
            template = templates.get(pricing_id)
            if source is None or template is None:
                continue
            new_row = copy.deepcopy(template)
            write_prices(new_row, source)
            inserts.append(new_row)
            day_changed = True
            changes.append(("insert", day, pricing_id, price_tuple(source)))

        if inserts:
            rows.extend(inserts)
        if day_changed:
            rewritten[day] = rows
    return rewritten, changes


def _source_for(pricing_id, direct, stable, presence, day):
    if pricing_id in direct:
        return direct[pricing_id]
    if pricing_id in stable and _inside(pricing_id, presence, day):
        return stable[pricing_id]
    return None


def _inside(pricing_id: str, presence: dict[str, set[str]], day: str) -> bool:
    days = presence.get(pricing_id) or set()
    if len(days) < 2:
        return False
    return min(days) <= day <= max(days)


def _fetch(url: str, attempts: int = 4) -> str:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "cofair-colonial-repair"})
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last_error)


def wayback_timestamps(start: str, end: str, page_url: str = PAGE_URL) -> dict[str, str]:
    """Last successful capture of ``page_url`` on each UTC day."""
    target = page_url.removeprefix("https://")
    query = (
        "https://web.archive.org/cdx/search/cdx?url="
        + target
        + "&output=json&fl=timestamp,statuscode&filter=statuscode:200"
        + f"&from={start.replace('-', '')}&to={end.replace('-', '')}"
    )
    payload = json.loads(_fetch(query))
    chosen: dict[str, str] = {}
    for timestamp, status in payload[1:]:
        if status != "200" or len(timestamp) < 8:
            continue
        day = f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"
        if start <= day <= end:
            chosen[day] = timestamp  # CDX is chronological; last one wins
    return chosen


def reparse_capture(timestamp: str, page_url: str = PAGE_URL) -> list[dict]:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scrape_pricing import aggregate_tier_rows, parse_openai

    cache = Path("/tmp/oa-wayback") / f"{timestamp}.txt"
    if cache.exists():
        text = cache.read_text()
    else:
        text = _fetch(f"https://web.archive.org/web/{timestamp}id_/{page_url}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text)
    rows = aggregate_tier_rows(parse_openai(text))
    return [row for row in rows if is_standard_flagship(row)]


def _load_window() -> tuple[dict[str, dict], dict[str, list[dict]], dict[str, list[dict]]]:
    docs = {}
    pre = {}
    trusted = {}
    for path in sorted(HISTORY_DIR.glob("*.json")):
        day = path.stem
        if not (REPAIR_START <= day or TRUSTED_FROM <= day):
            continue
        if day > "2026-10-19":
            continue
        doc = json.loads(path.read_text())
        docs[day] = doc
        rows = doc.get("pricing", [])
        if REPAIR_START <= day <= REPAIR_END:
            pre[day] = rows
        elif day >= TRUSTED_FROM:
            trusted[day] = rows
    return docs, pre, trusted


def _assert_expected(pre: dict[str, list[dict]], rewritten: dict[str, list[dict]]) -> None:
    for day, original in pre.items():
        rows = rewritten.get(day, original)
        found = index_flagship(rows)
        for pricing_id, output in EXPECTED_OUTPUT.items():
            row = found.get(pricing_id)
            if row is None or price_tuple(row)[2] != output:
                actual = None if row is None else row.get("output_price")
                raise SystemExit(
                    f"refusing to write {day}: {pricing_id} output is {actual}, expected {output}"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="plan the repair and print it, write nothing")
    args = parser.parse_args(argv)

    docs, pre, trusted = _load_window()
    if not pre:
        print("no snapshots in the repair window", file=sys.stderr)
        return 1

    stamps = wayback_timestamps(REPAIR_START, REPAIR_END)
    print(f"wayback HTML captures: {len(stamps)} of {len(pre)} days")
    reparsed: dict[str, list[dict]] = {}
    for day in sorted(pre):
        stamp = stamps.get(day)
        if not stamp:
            print(f"  {day}: no HTML capture")
            continue
        try:
            rows = reparse_capture(stamp)
        except Exception as exc:  # network or a capture that is not the pricing page
            print(f"  {day}: HTML capture failed ({exc})")
            continue
        if not rows:
            print(f"  {day}: HTML capture had no standard flagship rows")
            continue
        reparsed[day] = rows
        print(f"  {day}: {len(rows)} standard flagship rows from HTML")

    # The .md mirror is the full card. Where an archive exists it replaces the
    # truncated HTML reading for that day, which is what puts GPT-6.1 Sol on
    # the days the shell had already dropped it.
    md_query_days = wayback_timestamps(REPAIR_START, REPAIR_END, PAGE_URL + ".md")
    print(f"wayback markdown captures: {len(md_query_days)}")
    for day, stamp in sorted(md_query_days.items()):
        try:
            rows = reparse_capture(stamp, PAGE_URL + ".md")
        except Exception as exc:
            print(f"  {day}: markdown capture failed ({exc})")
            continue
        if not rows:
            print(f"  {day}: markdown capture had no standard flagship rows")
            continue
        reparsed[day] = rows
        print(f"  {day}: {len(rows)} standard flagship rows from markdown")

    if not reparsed:
        print("no archived pages reparsed; leaving snapshots untouched", file=sys.stderr)
        return 1

    rewritten, changes = repair_snapshots(pre, reparsed, trusted)
    updates = sum(1 for kind, *_rest in changes if kind == "update")
    inserts = sum(1 for kind, *_rest in changes if kind == "insert")
    print(f"plan: {updates} price corrections, {inserts} restored rows, {len(rewritten)} files")
    _assert_expected(pre, rewritten)

    if args.dry_run:
        for kind, day, pricing_id, prices in changes:
            if "luna" in pricing_id or "astra" in pricing_id or "6-sol" in pricing_id or "6.1-sol" in pricing_id:
                print(f"  {kind:6} {day} {pricing_id} {prices}")
        return 0

    for day, rows in rewritten.items():
        path = HISTORY_DIR / f"{day}.json"
        original = path.read_text()
        docs[day]["pricing"] = rows
        text = json.dumps(docs[day], indent=2)
        if original.endswith("\n"):
            text += "\n"
        path.write_text(text)
        print(f"wrote {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
