#!/usr/bin/env python3
"""HTTP helpers for colonial provider adapters — retries with backoff."""
from __future__ import annotations

import os
import random
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ops.provider_faults import classify_provider_error

# Status codes worth retrying when the body is not an account fault.
_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})

_DEFAULT_MAX_ATTEMPTS = 4
_DEFAULT_MAX_BACKOFF = 30.0


def _max_attempts() -> int:
    raw = os.getenv("COFAIR_HTTP_MAX_ATTEMPTS")
    if not raw:
        return _DEFAULT_MAX_ATTEMPTS
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_MAX_ATTEMPTS


def _max_backoff() -> float:
    raw = os.getenv("COFAIR_HTTP_MAX_BACKOFF")
    if not raw:
        return _DEFAULT_MAX_BACKOFF
    try:
        return max(0.5, float(raw))
    except ValueError:
        return _DEFAULT_MAX_BACKOFF


def _retry_after_seconds(response: requests.Response) -> float | None:
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(header).timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


def _should_retry_response(response: requests.Response) -> bool:
    if response.status_code not in _RETRYABLE_STATUSES:
        return False
    try:
        body = response.text
    except Exception:  # noqa: BLE001
        body = ""
    category = classify_provider_error(response.status_code, body)
    # Account faults often arrive as 429; retrying them only burns time.
    return category == "transient"


def _sleep_before_retry(attempt: int, response: requests.Response | None) -> None:
    if response is not None:
        retry_after = _retry_after_seconds(response)
        if retry_after is not None:
            time.sleep(min(retry_after, _max_backoff()))
            return
    base = min(_max_backoff(), 0.5 * (2 ** attempt))
    jitter = random.uniform(0, base * 0.25)
    time.sleep(base + jitter)


def request_with_retry(method: str, url: str, **kwargs: Any) -> requests.Response:
    """Issue an HTTP request, retrying transient provider faults.

    Returns the final response (does not raise on HTTP error status) so callers
    such as Anthropic's max_tokens ladder can inspect 400 bodies directly.
    """
    timeout = kwargs.pop("timeout", None)
    max_attempts = _max_attempts()
    last_response: requests.Response | None = None

    for attempt in range(max_attempts):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt >= max_attempts - 1:
                raise
            _sleep_before_retry(attempt, None)
            continue

        last_response = response
        if _should_retry_response(response) and attempt < max_attempts - 1:
            _sleep_before_retry(attempt, response)
            continue
        return response

    assert last_response is not None
    return last_response
