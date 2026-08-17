"""Deterministic runbooks for colonial pricing scrape failures."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def runbook_unit_keyerror() -> tuple[bool, list[str], int]:
    """Tier-1: apply tier-safe sort key fix in scrape_pricing.py if not present."""
    actions: list[str] = []
    target = ROOT / "scrape_pricing.py"
    text = target.read_text()

    if "def _tier_sort_key" in text:
        actions.append("tier_sort_key_already_present")
        return True, actions, 1

    if "merged.sort(key=_pricing_sort_key)" not in text:
        actions.append("remediate_sort_pattern_not_found")
        return False, actions, 1

    tier_key_fn = '''
def _tier_sort_key(row):
    """Sort key for aggregated tier rows (no component `unit` field)."""
    return (
        row["provider_id"],
        row["display_name"].lower(),
        row.get("service_tier") or "",
        row.get("context_window") or "",
        row.get("modality") or "",
        row.get("billing_variant") or "",
    )


'''
    insert_at = text.find("def _pricing_sort_key")
    if insert_at < 0:
        actions.append("_pricing_sort_key_not_found")
        return False, actions, 1

    text = text[:insert_at] + tier_key_fn + text[insert_at:]
    text = text.replace(
        "merged.sort(key=_pricing_sort_key)",
        "merged.sort(key=_tier_sort_key)",
        1,
    )
    target.write_text(text)
    actions.append("patched_tier_sort_key_in_remediate")
    return True, actions, 1


def runbook_retry_scrape() -> tuple[bool, list[str], int]:
    """Tier-1: rerun scrape once after transient failure."""
    actions = ["retry_scrape"]
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scrape_pricing.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    actions.append(f"exit_code={proc.returncode}")
    if proc.returncode != 0:
        actions.append(proc.stderr[-500:] if proc.stderr else proc.stdout[-500:])
    return proc.returncode == 0, actions, 1


def execute_runbook(signature: str) -> tuple[bool, list[str], int]:
    if signature == "KeyError:unit":
        return runbook_unit_keyerror()
    if signature == "SanityCheckFailed":
        return runbook_retry_scrape()
    return False, ["no_runbook_for_signature"], 0

