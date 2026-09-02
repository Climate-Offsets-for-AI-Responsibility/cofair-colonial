"""COFAIR ops utilities for colonial scheduled pipelines."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INCIDENTS_DIR = Path("ops/incidents")
LATEST_INCIDENT_PATH = INCIDENTS_DIR / "latest.json"
SCHEMA_VERSION = "1.0.0"


def now_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def classify_error(message: str) -> tuple[str, str]:
    text = (message or "").strip()
    key_match = re.search(r"KeyError:\s*['\"]?([^'\"]+)['\"]?", text)
    if key_match:
        return f"KeyError:{key_match.group(1)}", "parse"
    if re.search(r"sanity check failed", text, re.I):
        return "SanityCheckFailed", "parse"
    if re.search(
        r"billing_not_active|account is not active|insufficient_quota|invalid_api_key|"
        r"credit balance is too low|payment required|ProviderAccountFault",
        text,
        re.I,
    ):
        return "ProviderAccountFault", "account"
    if re.search(r"auth|401|403|unauthorized|forbidden", text, re.I):
        return "AuthError", "auth"
    if re.search(r"TransientProviderFault|overloaded_error|529", text, re.I):
        return "TransientProviderFault", "rate_limit"
    if re.search(r"rate.?limit|429|quota", text, re.I):
        return "RateLimit", "rate_limit"
    if re.search(r"timeout|timed out", text, re.I):
        return "Timeout", "timeout"
    if re.search(r"Failed after \d+ attempts", text):
        return "NetworkRetryExhausted", "network"
    first = re.sub(r"[^a-zA-Z0-9:_-]+", "_", text.split("\n")[0][:120]).strip("_")[:80]
    return first or "UnknownError", "unknown"


def gha_context() -> dict[str, str | None]:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    sha = os.environ.get("GITHUB_SHA")
    workflow = os.environ.get("GITHUB_WORKFLOW")
    url = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else None
    return {
        "repo": repo or "cofair-colonial",
        "workflow_run_id": run_id,
        "workflow_run_url": url,
        "commit_sha": sha,
        "workflow": workflow or os.environ.get("COFAIR_PIPELINE_WORKFLOW", "unknown"),
    }


def build_incident_envelope(
    *,
    pipeline: str,
    status: str,
    run_id: str,
    started_at: str,
    finished_at: str | None = None,
    error_message: str | None = None,
    failed_step: str | None = None,
    remediation: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    incident_id: str | None = None,
) -> dict[str, Any]:
    ctx = gha_context()
    signature, category = classify_error(error_message or "") if error_message else (None, None)
    finished = finished_at or now_iso_z()
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "incident_id": incident_id or str(uuid.uuid4()),
        "run_id": run_id,
        "pipeline": pipeline,
        "repo": ctx["repo"] or "cofair-colonial",
        "workflow": ctx["workflow"] or "unknown",
        "status": status,
        "started_at": started_at,
        "finished_at": finished,
        "commit_sha": ctx["commit_sha"],
        "workflow_run_id": ctx["workflow_run_id"],
        "workflow_run_url": ctx["workflow_run_url"],
        "artifact_links": [],
        "remediation": remediation or {
            "attempted": False,
            "actions": [],
            "result": None,
            "autonomy_tier": 0,
        },
        "verification": verification or {
            "passed": status == "success",
            "checks": [],
        },
        "context": context or {},
    }
    if failed_step:
        envelope["failed_step"] = failed_step
    if error_message:
        envelope["error_message"] = error_message
        envelope["error_signature"] = signature
        envelope["error_category"] = category
    if ctx["workflow_run_url"]:
        envelope["artifact_links"] = [f"{ctx['workflow_run_url']}#artifacts"]
    return envelope


def write_incident(envelope: dict[str, Any]) -> Path:
    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_INCIDENT_PATH.write_text(json.dumps(envelope, indent=2))
    if envelope.get("status") in ("failed", "degraded", "escalated"):
        day = envelope["finished_at"][:10]
        history_dir = INCIDENTS_DIR / day
        history_dir.mkdir(parents=True, exist_ok=True)
        history_path = history_dir / f"{envelope['run_id']}.json"
        history_path.write_text(json.dumps(envelope, indent=2))
    return LATEST_INCIDENT_PATH


def ops_process_label(source: str | None = None) -> str:
    label = (
        source
        or os.environ.get("COFAIR_OPS_SOURCE")
        or os.environ.get("COFAIR_PIPELINE")
        or "unknown"
    )
    return f"[COFAIR ops | {label}]"


def format_slack_context(
    *,
    headline: str,
    run_id: str,
    extra_lines: list[str] | None = None,
    pipeline: str | None = None,
) -> str:
    ctx = gha_context()
    lines = [f"{ops_process_label(pipeline)} {headline}", f"run_id: {run_id}"]
    if ctx["workflow_run_url"]:
        lines.append(f"workflow: {ctx['workflow_run_url']}")
    if ctx["commit_sha"]:
        lines.append(f"commit: {ctx['commit_sha'][:12]}")
    if extra_lines:
        lines.extend(extra_lines)
    lines.append(f"Time: {now_iso_z()}")
    return "\n".join(lines)


def send_slack_message(text: str, pipeline: str | None = None) -> bool:
    label = ops_process_label(pipeline)
    if not text.strip().startswith("[COFAIR ops |"):
        text = f"{label}\n{text}"
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL", "#notifications")
    if not token:
        print("SLACK WARNING: Missing SLACK_BOT_TOKEN; skipping notification.")
        return False

    import requests

    try:
        response = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"channel": channel, "text": text},
            timeout=20,
        )
        payload = response.json()
        if not payload.get("ok"):
            print(f"SLACK WARNING: {payload}")
            return False
        return True
    except Exception as exc:
        print(f"SLACK WARNING: {exc}")
        return False
