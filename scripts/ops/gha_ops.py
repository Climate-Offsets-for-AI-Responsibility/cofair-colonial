#!/usr/bin/env python3
"""GHA ops CLI: notify failures, run remediation runbooks, open auto-fix PRs."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ops"))

from incident import (  # noqa: E402
    build_incident_envelope,
    classify_error,
    format_slack_context,
    now_iso_z,
    send_slack_message,
    write_incident,
)
from runbooks import execute_runbook  # noqa: E402

ALLOWLIST_AUTOFIX = {"KeyError:unit"}


def _load_run_report() -> dict:
    report_path = ROOT / "run_report.json"
    if not report_path.exists():
        return {}
    try:
        return json.loads(report_path.read_text())
    except json.JSONDecodeError:
        return {}


def cmd_notify_failure(args: argparse.Namespace) -> int:
    report = _load_run_report()
    run_id = args.run_id or report.get("run_id") or str(uuid.uuid4())
    started = args.started_at or now_iso_z()
    status = report.get("status", "failed")
    error = args.error or report.get("error")
    if not error and status == "degraded":
        remediation = report.get("remediation") or {}
        providers = remediation.get("providers") or []
        error = (
            "self-heal fallback applied for providers: "
            + ", ".join(providers)
            if providers
            else "degraded run (self-heal fallback applied)"
        )
    if not error:
        error = "workflow step failed"
    signature, category = classify_error(error)

    envelope = build_incident_envelope(
        pipeline=args.pipeline,
        status=status if status in ("failed", "degraded", "escalated") else "failed",
        run_id=run_id,
        started_at=started,
        error_message=error,
        failed_step=args.failed_step,
    )
    write_incident(envelope)

    send_slack_message(
        format_slack_context(
            headline=f"Pipeline FAILED: {args.pipeline}",
            run_id=run_id,
            pipeline=args.pipeline,
            extra_lines=[
                f"step: {args.failed_step or 'unknown'}",
                f"error: {error}",
                f"signature: {signature} ({category})",
            ],
        ),
        pipeline=args.pipeline,
    )
    return 0


def cmd_remediate(args: argparse.Namespace) -> int:
    from verify_pricing_run import verify_pricing_run  # noqa: WPS433

    signature = args.signature
    run_id = args.run_id or str(uuid.uuid4())
    started = args.started_at or now_iso_z()

    ok, actions, tier = execute_runbook(signature)
    remediation = {
        "attempted": True,
        "actions": actions,
        "result": "success" if ok else "failed",
        "autonomy_tier": tier,
    }

    verification = {"passed": False, "checks": []}
    if ok and signature == "KeyError:unit":
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scrape_pricing.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        remediation["actions"].append(f"post_fix_scrape_exit={proc.returncode}")
        if proc.returncode == 0:
            verification = verify_pricing_run()

    status = "success" if verification.get("passed") else "escalated" if not ok else "failed"
    envelope = build_incident_envelope(
        pipeline=args.pipeline,
        status=status,
        run_id=run_id,
        started_at=started,
        error_message=args.error_message,
        remediation=remediation,
        verification=verification,
    )
    write_incident(envelope)

    if verification.get("passed"):
        send_slack_message(
            format_slack_context(
                headline=f"Auto-remediation SUCCESS: {args.pipeline}",
                run_id=run_id,
                pipeline=args.pipeline,
                extra_lines=[f"signature: {signature}", f"actions: {', '.join(actions)}"],
            ),
            pipeline=args.pipeline,
        )
        return 0

    send_slack_message(
        format_slack_context(
            headline=f"Auto-remediation FAILED: {args.pipeline}",
            run_id=run_id,
            pipeline=args.pipeline,
            extra_lines=[
                f"signature: {signature}",
                f"actions: {', '.join(actions)}",
            ],
        ),
        pipeline=args.pipeline,
    )
    return 1


def cmd_autofix_pr(args: argparse.Namespace) -> int:
    signature = args.signature
    if signature not in ALLOWLIST_AUTOFIX:
        print(f"Signature {signature} not on autofix allowlist")
        return 1

    branch = f"ops/autofix-{signature.replace(':', '-')}-{args.run_id[:8]}"
    subprocess.run(["git", "config", "user.name", "github-actions"], check=True, cwd=ROOT)
    subprocess.run(["git", "config", "user.email", "github-actions@github.com"], check=True, cwd=ROOT)
    subprocess.run(["git", "checkout", "-b", branch], check=True, cwd=ROOT)

    ok, actions, _ = execute_runbook(signature)
    if not ok:
        print("Runbook failed before PR:", actions)
        return 1

    subprocess.run(["git", "add", "scrape_pricing.py"], cwd=ROOT, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if diff.returncode == 0:
        print("No code changes to commit for autofix PR")
        return 0

    subprocess.run(
        [
            "git",
            "commit",
            "-m",
            f"ops: autofix {signature} (run_id={args.run_id})",
        ],
        cwd=ROOT,
        check=True,
    )

    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "test_scrape_resilience.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if tests.returncode != 0:
        print("Tests failed; not pushing autofix branch")
        print(tests.stdout)
        print(tests.stderr)
        return 1

    subprocess.run(["git", "push", "-u", "origin", branch], cwd=ROOT, check=True)

    gh = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "Climate-Offsets-for-AI-Responsibility/cofair-colonial")
    pr_url = f"{gh}/{repo}/compare/{branch}?expand=1"
    send_slack_message(
        format_slack_context(
            headline=f"Auto-fix PR ready: {signature}",
            run_id=args.run_id,
            pipeline="colonial-pricing-ops-remediate",
            extra_lines=[f"branch: {branch}", f"open PR: {pr_url}"],
        ),
        pipeline="colonial-pricing-ops-remediate",
    )
    print(json.dumps({"branch": branch, "pr_url": pr_url}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Colonial GHA ops")
    sub = parser.add_subparsers(dest="command", required=True)

    notify = sub.add_parser("notify-failure")
    notify.add_argument("--pipeline", required=True)
    notify.add_argument("--failed-step", default=None)
    notify.add_argument("--error", default=None)
    notify.add_argument("--run-id", default=None)
    notify.add_argument("--started-at", default=None)
    notify.set_defaults(func=cmd_notify_failure)

    remediate = sub.add_parser("remediate")
    remediate.add_argument("--pipeline", default="colonial-daily-scrape")
    remediate.add_argument("--signature", required=True)
    remediate.add_argument("--run-id", default=str(uuid.uuid4()))
    remediate.add_argument("--started-at", default=None)
    remediate.add_argument("--error-message", default=None)
    remediate.set_defaults(func=cmd_remediate)

    autofix = sub.add_parser("autofix-pr")
    autofix.add_argument("--signature", required=True)
    autofix.add_argument("--run-id", default=str(uuid.uuid4()))
    autofix.set_defaults(func=cmd_autofix_pr)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
