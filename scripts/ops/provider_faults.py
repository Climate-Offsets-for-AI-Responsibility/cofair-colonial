"""Classify provider API faults for token pipelines and ops remediation."""
from __future__ import annotations

import json
import re
from typing import Any

FaultCategory = str  # transient | account | model_unavailable | fault

_ACCOUNT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"billing_not_active", re.I), "billing_not_active"),
    (re.compile(r"account is not active", re.I), "billing_not_active"),
    (re.compile(r"insufficient_quota", re.I), "insufficient_quota"),
    (re.compile(r"exceeded your current quota", re.I), "insufficient_quota"),
    (re.compile(r"invalid_api_key", re.I), "invalid_api_key"),
    (re.compile(r"incorrect api key", re.I), "invalid_api_key"),
    (re.compile(r"credit balance is too low", re.I), "credit_balance_low"),
    (re.compile(r"payment required", re.I), "payment_required"),
]

_MODEL_UNAVAILABLE_PATTERNS = (
    "not_found",
    "not found",
    "does not exist",
    "invalid",
    "legacy",
    "access denied",
    "no access",
    "not authorized",
    "unsupported model",
)

_TRANSIENT_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})

REMEDY: dict[str, str] = {
    "billing_not_active": (
        "OpenAI account inactive — settle billing at "
        "platform.openai.com/settings/organization/billing"
    ),
    "insufficient_quota": "Provider quota exhausted — raise limits or wait for reset.",
    "invalid_api_key": "API key rejected — rotate the provider secret in CI and shared env.",
    "credit_balance_low": "Provider credit balance too low — add funds on the provider console.",
    "payment_required": "Provider requires payment — update billing on the provider console.",
    "TransientProviderFault": (
        "Transient provider fault — pipeline will retry automatically; escalate if persistent."
    ),
    "ProviderAccountFault": "Provider account fault — human action required on the provider console.",
}


def _extract_error_code(body: str) -> str | None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code") or error.get("type")
        if code:
            return str(code)
    return None


def account_signature(body: str) -> str | None:
    """Return a stable account-fault signature when the body matches."""
    code = _extract_error_code(body)
    if code:
        for pattern, signature in _ACCOUNT_PATTERNS:
            if pattern.search(code):
                return signature
    lowered = body.lower()
    for pattern, signature in _ACCOUNT_PATTERNS:
        if pattern.search(lowered):
            return signature
    return None


def classify_provider_error(status: int | None, body: str) -> FaultCategory:
    """Map an HTTP status + body to a fault category."""
    if body:
        if account_signature(body):
            return "account"
        lowered = body.lower()
        if status in (403, 404):
            return "model_unavailable"
        if status in (400, 422) and "model" in lowered:
            if any(p in lowered for p in _MODEL_UNAVAILABLE_PATTERNS):
                return "model_unavailable"
    if status in (401, 403) and body:
        lowered = body.lower()
        if any(
            phrase in lowered
            for phrase in ("unauthorized", "forbidden", "api key", "authentication", "permission")
        ):
            if account_signature(body):
                return "account"
            return "account"
    if status in _TRANSIENT_STATUSES:
        if body and account_signature(body):
            return "account"
        return "transient"
    if status in (403, 404):
        return "model_unavailable"
    if status in (400, 422) and body:
        lowered = body.lower()
        if "model" in lowered and any(p in lowered for p in _MODEL_UNAVAILABLE_PATTERNS):
            return "model_unavailable"
    return "fault"


def remedy_for_error(body: str, provider_id: str | None = None) -> str:
    signature = account_signature(body) or "ProviderAccountFault"
    if signature in REMEDY:
        return REMEDY[signature]
    label = provider_id or "provider"
    return f"{label} account fault — check billing and API credentials on the provider console."


def error_signature_from_body(body: str, status: int | None = None) -> str:
    account = account_signature(body)
    if account:
        return f"ProviderAccountFault:{account}"
    category = classify_provider_error(status, body)
    if category == "transient":
        return "TransientProviderFault"
    if category == "model_unavailable":
        return "ModelUnavailable"
    if category == "account":
        return "ProviderAccountFault"
    first = re.sub(r"[^a-zA-Z0-9:_-]+", "_", body.split("\n")[0][:80]).strip("_")
    return first or "UnknownProviderFault"


def run_status_for_http(status: int | None, body: str) -> str:
    return run_status_for_category(classify_provider_error(status, body))


def run_status_for_category(category: FaultCategory) -> str:
    if category == "account":
        return "provider_unavailable"
    return "error"


def fault_report_entry(
    *,
    provider_id: str,
    tier: str,
    source: str,
    status: int | None,
    body: str,
    error_message: str,
) -> dict[str, Any]:
    signature = error_signature_from_body(body, status)
    category = classify_provider_error(status, body)
    return {
        "provider_id": provider_id,
        "tier": tier,
        "source": source,
        "category": category,
        "signature": signature,
        "remedy": remedy_for_error(body, provider_id),
        "error": error_message,
    }
