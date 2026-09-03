#!/usr/bin/env python3
"""Shared provider adapters for input-token counting (no meaningful generation).

Used by the tokenizer ledger and Test 4 wrapper runner. Prefer dedicated count
APIs; fall back to max_tokens=1 and read usage.prompt / input tokens.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import requests

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ops.provider_faults import classify_provider_error, run_status_for_http
from provider_http import request_with_retry

TIMEOUT_SECONDS = 90

# api.openai.com rejects both `max_tokens` and `temperature: 0` on its current
# chat models, and refuses `max_completion_tokens: 1` outright ("could not
# finish the message"). Counting only needs usage.prompt_tokens, so ask for the
# smallest cap the API will actually accept and throw the completion away.
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_MIN_OUTPUT_TOKENS = 16

QWEN_MODEL_MAP = {
    "qwen3.7-max": "qwen-max",
    "qwen3.7-plus": "qwen-plus",
    "qwen-flash": "qwen-turbo",
}

# Scrape model_id → Bedrock Runtime / Converse modelId. Prefer the on-demand
# amazon.* form when it works; use us.* inference profiles when on-demand is
# refused (Nova 2 Lite, Premier). Region comes from BEDROCK_REGION / AWS_REGION.
BEDROCK_MODEL_MAP = {
    "nova-micro": "amazon.nova-micro-v1:0",
    "nova-lite": "amazon.nova-lite-v1:0",
    "nova-pro": "amazon.nova-pro-v1:0",
    "nova-premier": "us.amazon.nova-premier-v1:0",
    "nova-2.0-lite": "us.amazon.nova-2-lite-v1:0",
    "nova-2-lite": "us.amazon.nova-2-lite-v1:0",
    "nova-2.0-pro": "us.amazon.nova-2-pro-v1:0",
    "nova-2-pro": "us.amazon.nova-2-pro-v1:0",
}


def bedrock_region() -> str:
    return os.getenv("BEDROCK_REGION") or os.getenv("AWS_REGION") or "us-east-1"


def env_for_provider(provider_id: str) -> str | None:
    """Resolve a provider credential from env.

    The shared hub env (``../cofair/.env/.env.cofair``) stores tracker keys as
    ``TRACKER_<NAME>_API_KEY`` so they do not collide with exchange / M0 tracer
    keys that already occupy the unprefixed names. CI and local ``.env`` may
    still use the unprefixed forms; prefer TRACKER_* when both are set.
    """
    if provider_id == "google":
        return (
            os.getenv("TRACKER_GEMINI_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )
    if provider_id == "aws":
        # TRACKER_AMAZON_API_KEY matches the dashboard's "Amazon" display label;
        # AWS_BEARER_TOKEN_BEDROCK is the name AWS documents for Bedrock API keys.
        return (
            os.getenv("TRACKER_AMAZON_API_KEY")
            or os.getenv("TRACKER_AWS_BEARER_TOKEN_BEDROCK")
            or os.getenv("AWS_BEARER_TOKEN_BEDROCK")
            or os.getenv("AWS_ACCESS_KEY_ID")
        )
    mapping = {
        "anthropic": ("TRACKER_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        "openai": ("TRACKER_OPENAI_API_KEY", "OPENAI_API_KEY"),
        "xai": ("TRACKER_XAI_API_KEY", "XAI_API_KEY"),
        "deepseek": ("TRACKER_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
        "qwen": ("TRACKER_QWEN_API_KEY", "QWEN_API_KEY"),
    }
    keys = mapping.get(provider_id)
    if not keys:
        return None
    for key in keys:
        val = os.getenv(key)
        if val:
            return val
    return None


def resolve_api_model(provider_id: str, model_id: str, tier: str, api_key: str | None) -> str:
    candidates = api_model_candidates(provider_id, model_id, tier, api_key)
    return candidates[0] if candidates else model_id


_ANTHROPIC_MODEL_IDS: list[str] | None = None


def anthropic_model_ids(api_key: str) -> list[str]:
    """Ids Anthropic will actually serve on this key, newest first."""
    global _ANTHROPIC_MODEL_IDS
    if _ANTHROPIC_MODEL_IDS is not None:
        return _ANTHROPIC_MODEL_IDS
    response = request_with_retry(
        "GET",
        "https://api.anthropic.com/v1/models?limit=100",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        timeout=TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise _http_error_from_response(response)
    _ANTHROPIC_MODEL_IDS = [item["id"] for item in response.json().get("data", []) if item.get("id")]
    return _ANTHROPIC_MODEL_IDS


def _anthropic_candidates(model_id: str, api_key: str | None) -> list[str]:
    """Map a scraped marketing id onto a callable Anthropic api id.

    The price catalog carries dotted names (``claude-haiku-4.5``) while the API
    serves dashed ones, sometimes only as a dated snapshot
    (``claude-haiku-4-5-20251001``). Resolution stays inside the same pinned
    model — never a different family — so a substitution cannot quietly change
    what the drift series is measuring.
    """
    dashed = model_id.replace(".", "-")
    if not api_key:
        return [dashed]
    try:
        ids = anthropic_model_ids(api_key)
    except Exception:  # noqa: BLE001 — availability lookup is best-effort
        return [dashed]
    out = [i for i in ids if i == dashed]
    dated = sorted((i for i in ids if i.startswith(f"{dashed}-")), reverse=True)
    out.extend(i for i in dated if i not in out)
    return out or [dashed]


def api_model_candidates(
    provider_id: str,
    model_id: str,
    tier: str,
    api_key: str | None,
    fallback_model_ids: list[Any] | None = None,
) -> list[str]:
    """Ordered api model ids to try for one panel row.

    A pinned model can be present in the price catalog and still be uncallable —
    Anthropic serves dated ids, and Bedrock refuses ids that are Legacy on the
    key or absent from the region. `fallback_model_ids` carries the rest of the
    tier's preference list (from TIER_CANDIDATES via `equivalence.json`) so the
    caller can fall through instead of reporting the whole provider as dark.
    """
    if provider_id == "anthropic":
        return _anthropic_candidates(model_id, api_key)
    ordered: list[str] = []
    for entry in [model_id, *(fallback_model_ids or [])]:
        # Accepts bare ids or `equivalence.json`'s priced candidate rows.
        candidate = entry["model_id"] if isinstance(entry, dict) else entry
        if provider_id == "qwen":
            mapped = QWEN_MODEL_MAP.get(candidate, candidate)
        elif provider_id == "aws":
            mapped = BEDROCK_MODEL_MAP.get(candidate, candidate)
        else:
            mapped = candidate
        if mapped not in ordered:
            ordered.append(mapped)
    return ordered or [model_id]


# Bodies that mean "this model id is not usable on this key/region", as opposed
# to "the request was malformed". Only these justify trying the next candidate;
# a genuine parameter error must surface instead of being retried seven times.
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


def _http_error_from_response(response: requests.Response) -> requests.HTTPError:
    exc = requests.HTTPError(f"{response.status_code} Client Error", response=response)
    return exc


def _status_for_http_error(exc: requests.HTTPError) -> str:
    response = exc.response
    if response is None:
        return "error"
    try:
        body = response.text
    except Exception:  # noqa: BLE001
        body = ""
    return run_status_for_http(response.status_code, body)


def _is_model_unavailable(exc: requests.HTTPError) -> bool:
    response = exc.response
    if response is None:
        return False
    try:
        body = response.text
    except Exception:  # noqa: BLE001
        body = ""
    return classify_provider_error(response.status_code, body) == "model_unavailable"


def _redact(msg: str) -> str:
    return re.sub(r"(key=)[^&\s]+", r"\1[REDACTED]", msg)


def _http_error_message(exc: requests.HTTPError) -> str:
    detail = ""
    if exc.response is not None:
        try:
            detail = exc.response.text[:400]
        except Exception:  # noqa: BLE001
            detail = ""
    return _redact(f"{exc} :: {detail}".strip(" :"))


def normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Coerce corpus turns onto the chat-API shape.

    `CHAT_TRANSCRIPT` stores turns as ``{role, text}``, but every chat endpoint
    except Bedrock and Gemini requires ``content``. Passing the corpus shape
    straight through made Anthropic and all four OpenAI-compatible providers
    reject the whole Test 4 run, which read on the dashboard as "no data" rather
    than as a bug. Normalize once, at the boundary.
    """
    normalized: list[dict[str, str]] = []
    for message in messages:
        content = message.get("content")
        if content is None:
            content = message.get("text", "")
        normalized.append({"role": message.get("role", "user"), "content": str(content)})
    return normalized


OPENAI_COMPATIBLE_BASES = {
    "openai": OPENAI_BASE_URL,
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}


def _count_one(
    provider_id: str,
    api_model: str,
    payload: Any,
    api_key: str,
    is_messages: bool,
    tier: str | None = None,
):
    if provider_id == "anthropic":
        counter = _anthropic_count_messages if is_messages else _anthropic_count_text
        return counter(api_model, payload, api_key), api_model
    if provider_id == "google":
        # Gemini is the one provider whose callable id has to be discovered at
        # runtime: the panel pins ids from the price catalog (gemini-3.1-pro) that
        # `/v1beta/models` does not serve, so the preferred id misses and the tier
        # hint alone decides what we fall back to. Hard-coding it here — workhorse
        # for text, flagship for messages — meant the caller's tier was ignored,
        # and google flagship was counted on gemini-flash-latest for the whole
        # ledger: the workhorse model reported under both tiers.
        if is_messages:
            return _gemini_count_messages(api_model, payload, api_key, tier_hint=tier or "flagship")
        return _gemini_count_text(api_model, payload, api_key, tier_hint=tier or "workhorse")
    if provider_id in OPENAI_COMPATIBLE_BASES:
        base = OPENAI_COMPATIBLE_BASES[provider_id]
        counter = _openai_compat_count_messages if is_messages else _openai_compat_count_text
        return counter(base, api_model, payload, api_key), api_model
    if provider_id == "aws":
        counter = _bedrock_count_messages if is_messages else _bedrock_count_text
        return counter(api_model, payload, api_key), api_model
    raise LookupError(f"provider {provider_id} not implemented")


def _count(
    provider_id: str,
    api_model: str,
    payload: Any,
    api_key: str,
    is_messages: bool,
    candidates: list[str] | None,
    tier: str | None = None,
) -> tuple[str, int | None, str | None, str]:
    attempts = [api_model, *[c for c in (candidates or []) if c != api_model]]
    last_error: str | None = None
    used = api_model
    for candidate in attempts:
        used = candidate
        try:
            tokens, used = _count_one(
                provider_id, candidate, payload, api_key, is_messages, tier=tier
            )
        except LookupError as exc:
            return "unsupported_provider", None, str(exc), candidate
        except requests.HTTPError as exc:
            last_error = _http_error_message(exc)
            if _is_model_unavailable(exc):
                continue
            return _status_for_http_error(exc), None, last_error, candidate
        except Exception as exc:  # noqa: BLE001
            return "error", None, str(exc), candidate
        return "ok", tokens, None, used
    return "error", None, last_error, used


def count_prompt_tokens_text(
    provider_id: str,
    api_model: str,
    text: str,
    api_key: str,
    candidates: list[str] | None = None,
    tier: str | None = None,
) -> tuple[str, int | None, str | None, str]:
    """Count tokens for a single user-text prompt. Returns status, tokens, error, model.

    Pass `tier` wherever the caller knows it: Gemini resolves its callable id from
    the live model list, and without the tier it cannot tell a flagship row from a
    workhorse one.
    """
    return _count(
        provider_id,
        api_model,
        text,
        api_key,
        is_messages=False,
        candidates=candidates,
        tier=tier,
    )


def count_prompt_tokens_messages(
    provider_id: str,
    api_model: str,
    messages: list[dict[str, Any]],
    api_key: str,
    candidates: list[str] | None = None,
    tier: str | None = None,
) -> tuple[str, int | None, str | None, str]:
    """Count tokens for a chat-message prefix (Test 4)."""
    return _count(
        provider_id,
        api_model,
        normalize_messages(messages),
        api_key,
        is_messages=True,
        candidates=candidates,
        tier=tier,
    )


def _anthropic_count_text(model: str, text: str, api_key: str) -> int:
    response = request_with_retry(
        "POST",
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": text}],
        },
        timeout=TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise _http_error_from_response(response)
    return int(response.json().get("usage", {}).get("input_tokens", 0))


def _anthropic_count_messages(model: str, messages: list[dict[str, str]], api_key: str) -> int:
    # Anthropic requires alternating roles ending with user for some paths;
    # if the prefix ends on assistant, append a tiny user nudge that we do not
    # bill as content of interest — still counts wrapper on the frozen prefix.
    api_messages = normalize_messages(messages)
    if api_messages and api_messages[-1]["role"] == "assistant":
        api_messages = api_messages + [{"role": "user", "content": "."}]
    response = request_with_retry(
        "POST",
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model": model, "max_tokens": 1, "messages": api_messages},
        timeout=TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise _http_error_from_response(response)
    return int(response.json().get("usage", {}).get("input_tokens", 0))


def openai_compatible_body(
    base_url: str, model: str, messages: list[dict[str, str]], max_tokens: int | None
) -> dict[str, Any]:
    """Chat-completions body for one OpenAI-compatible provider.

    api.openai.com has diverged from the shape the other three still accept: it
    requires `max_completion_tokens`, rejects `temperature: 0`, and will not
    accept a cap of 1. Keep the divergence in one place so a body built for
    OpenAI can never be sent to xAI/DeepSeek/Qwen, or vice versa.

    `max_tokens=None` omits the cap entirely (output policy 4.0.0), letting the
    model apply its own maximum. The count-only ledger still passes an explicit 1,
    so both regimes have to work: this is the one place that knows which key each
    base URL wants, and therefore the only place that can leave it out.
    """
    body: dict[str, Any] = {"model": model, "messages": messages}
    if base_url == OPENAI_BASE_URL:
        if max_tokens is not None:
            body["max_completion_tokens"] = max(max_tokens, OPENAI_MIN_OUTPUT_TOKENS)
    else:
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        body["temperature"] = 0
    return body


def _openai_compat_count_text(base_url: str, model: str, text: str, api_key: str) -> int:
    return _openai_compat_count_messages(
        base_url, model, [{"role": "user", "content": text}], api_key
    )


def _openai_compat_count_messages(
    base_url: str, model: str, messages: list[dict[str, str]], api_key: str
) -> int:
    response = request_with_retry(
        "POST",
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=openai_compatible_body(base_url, model, normalize_messages(messages), 1),
        timeout=TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise _http_error_from_response(response)
    return int(response.json().get("usage", {}).get("prompt_tokens", 0))


def _bedrock_messages(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Map {role, content|text} rows onto Bedrock Converse message shape."""
    return [
        {"role": msg["role"], "content": [{"text": msg["content"]}]}
        for msg in normalize_messages(messages)
    ]


def _bedrock_converse(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    max_tokens: int = 1,
) -> dict[str, Any]:
    region = bedrock_region()
    response = request_with_retry(
        "POST",
        f"https://bedrock-runtime.{region}.amazonaws.com/model/{model}/converse",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "messages": _bedrock_messages(messages),
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
        },
        timeout=TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise _http_error_from_response(response)
    return response.json()


def _bedrock_count_text(model: str, text: str, api_key: str) -> int:
    payload = _bedrock_converse(model, [{"role": "user", "content": text}], api_key)
    return int(payload.get("usage", {}).get("inputTokens", 0))


def _bedrock_count_messages(model: str, messages: list[dict[str, str]], api_key: str) -> int:
    # Converse wants the last message to be from the user for some models; if the
    # frozen transcript ends on assistant, nudge with a tiny user turn.
    api_messages = list(messages)
    if api_messages and api_messages[-1].get("role") == "assistant":
        api_messages = api_messages + [{"role": "user", "content": "."}]
    payload = _bedrock_converse(model, api_messages, api_key)
    return int(payload.get("usage", {}).get("inputTokens", 0))


def _gemini_count_text(
    model: str, text: str, api_key: str, tier_hint: str = "workhorse"
) -> tuple[int, str]:
    candidates = _gemini_candidates(api_key, tier_hint, preferred=model)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            tokens = _gemini_count_tokens(candidate, [{"role": "user", "parts": [{"text": text}]}], api_key)
            return tokens, candidate
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise RuntimeError("No callable Gemini model for countTokens")


def _gemini_count_messages(
    model: str, messages: list[dict[str, str]], api_key: str, tier_hint: str = "flagship"
) -> tuple[int, str]:
    contents = []
    for turn in normalize_messages(messages):
        role = "user" if turn["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": turn["content"]}]})
    candidates = _gemini_candidates(api_key, tier_hint, preferred=model)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            tokens = _gemini_count_tokens(candidate, contents, api_key)
            return tokens, candidate
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise RuntimeError("No callable Gemini model for countTokens")


def _gemini_count_tokens(model: str, contents: list[dict[str, Any]], api_key: str) -> int:
    response = request_with_retry(
        "POST",
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:countTokens",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json={"contents": contents},
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code in (404, 400):
        # Fall back to generateContent with maxOutputTokens=1.
        gen = request_with_retry(
            "POST",
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json={
                "contents": contents,
                "generationConfig": {"maxOutputTokens": 1, "temperature": 0},
            },
            timeout=TIMEOUT_SECONDS,
        )
        if not gen.ok:
            raise _http_error_from_response(gen)
        return int(gen.json().get("usageMetadata", {}).get("promptTokenCount", 0))
    if not response.ok:
        raise _http_error_from_response(response)
    payload = response.json()
    return int(payload.get("totalTokens") or payload.get("promptTokenCount") or 0)


def _gemini_candidates(api_key: str, tier_hint: str, preferred: str) -> list[str]:
    response = request_with_retry(
        "GET",
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": api_key},
        timeout=TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise _http_error_from_response(response)
    names = []
    for item in response.json().get("models", []):
        methods = item.get("supportedGenerationMethods", [])
        if "generateContent" not in methods and "countTokens" not in methods:
            continue
        name = (item.get("name") or "").replace("models/", "")
        if not name or "gemini" not in name:
            continue
        if any(bad in name for bad in ("embed", "tts", "vision", "image", "aqa")):
            continue
        names.append(name)
    preferred = preferred.replace("models/", "")
    ordered = []
    if preferred in names:
        ordered.append(preferred)
    if tier_hint == "flagship":
        ordered.extend(n for n in names if "pro" in n and n not in ordered)
    ordered.extend(n for n in names if "flash" in n and n not in ordered)
    ordered.extend(n for n in names if n not in ordered)
    return ordered[:8]
