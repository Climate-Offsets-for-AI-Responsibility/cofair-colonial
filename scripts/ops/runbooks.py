"""Deterministic runbooks for colonial pricing scrape failures."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


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


def _token_source_command(source: str, provider_id: str) -> list[str] | None:
    if source == "ledger":
        return [
            sys.executable,
            str(SCRIPTS / "run_tokenizer_ledger.py"),
            "--tasks",
            "all",
            "--mode",
            "two",
            "--provider",
            provider_id,
        ]
    if source == "meter":
        return [
            sys.executable,
            str(SCRIPTS / "run_equivalence_tasks.py"),
            "--mode",
            "two",
            "--workhorse-replicates",
            "1",
            "--provider",
            provider_id,
        ]
    return None


def runbook_retry_token_source(report: dict) -> tuple[bool, list[str], int]:
    """Re-run only the provider/source pairs that failed transiently."""
    actions: list[str] = ["retry_token_source"]
    targets = report.get("retry_targets") or []
    if not targets:
        actions.append("no_retry_targets")
        return False, actions, 1

    ok = True
    for target in targets:
        source = target.get("source")
        provider_id = target.get("provider_id")
        if not source or not provider_id:
            continue
        cmd = _token_source_command(source, provider_id)
        if cmd is None:
            actions.append(f"unknown_source={source}")
            ok = False
            continue
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        actions.append(f"{source}:{provider_id}:exit={proc.returncode}")
        if proc.returncode != 0:
            ok = False
            tail = proc.stderr[-400:] if proc.stderr else proc.stdout[-400:]
            if tail:
                actions.append(tail)

    rebuild = subprocess.run(
        [sys.executable, str(SCRIPTS / "build_dashboard_data.py"), "--rebuild"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    actions.append(f"rebuild_exit={rebuild.returncode}")
    if rebuild.returncode != 0:
        ok = False
        tail = rebuild.stderr[-400:] if rebuild.stderr else rebuild.stdout[-400:]
        if tail:
            actions.append(tail)

    verify = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "ops" / "verify_token_runs.py"),
            "--sources",
            "meter",
            "--sources",
            "ledger",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    actions.append(f"verify_exit={verify.returncode}")
    if verify.returncode != 0:
        ok = False
        tail = verify.stderr[-400:] if verify.stderr else verify.stdout[-400:]
        if tail:
            actions.append(tail)
    return ok, actions, 1


def runbook_load_token_report() -> dict:
    path = ROOT / "ops" / "token_run_report.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def execute_runbook(signature: str, report: dict | None = None) -> tuple[bool, list[str], int]:
    if signature == "KeyError:unit":
        return runbook_unit_keyerror()
    if signature == "SanityCheckFailed":
        return runbook_retry_scrape()
    if signature == "TransientProviderFault":
        payload = report or runbook_load_token_report()
        return runbook_retry_token_source(payload)
    if signature == "ProviderAccountFault":
        return False, ["account_fault_not_auto_remediable"], 0
    return False, ["no_runbook_for_signature"], 0

