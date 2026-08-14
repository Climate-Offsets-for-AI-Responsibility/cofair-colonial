#!/usr/bin/env python3
"""Shared provider adapters for input-token counting (no meaningful generation).

Used by the tokenizer ledger and Test 4 wrapper runner. Prefer dedicated count
APIs; fall back to max_tokens=1 and read usage.prompt / input tokens.
"""
from __future__ import annotations

import os
import re
from typing import Any

import requests

TIMEOUT_SECONDS = 90

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
    if provider_id == "qwen":
        return QWEN_MODEL_MAP.get(model_id, model_id)
    if provider_id == "aws":
        return BEDROCK_MODEL_MAP.get(model_id, model_id)
    if provider_id == "google" and api_key:
        # Prefer an available chat model; callers may still retry candidates.
        return model_id
    return model_id


def _redact(msg: str) -> str:
    return re.sub(r"(key=)[^&\s]+", r"\1[REDACTED]", msg)


def count_prompt_tokens_text(
    provider_id: str,
    api_model: str,
    text: str,
    api_key: str,
) -> tuple[str, int | None, str | None, str]:
    """Count tokens for a single user-text prompt. Returns status, tokens, error, model."""
    try:
        if provider_id == "anthropic":
            tokens = _anthropic_count_text(api_model, text, api_key)
        elif provider_id == "google":
            tokens, api_model = _gemini_count_text(api_model, text, api_key, tier_hint="workhorse")
        elif provider_id == "openai":
            tokens = _openai_compat_count_text("https://api.openai.com/v1", api_model, text, api_key)
        elif provider_id == "xai":
            tokens = _openai_compat_count_text("https://api.x.ai/v1", api_model, text, api_key)
        elif provider_id == "deepseek":
            tokens = _openai_compat_count_text("https://api.deepseek.com", api_model, text, api_key)
        elif provider_id == "qwen":
            tokens = _openai_compat_count_text(
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                api_model,
                text,
                api_key,
            )
        elif provider_id == "aws":
            tokens = _bedrock_count_text(api_model, text, api_key)
        else:
            return "unsupported_provider", None, f"provider {provider_id} not implemented", api_model
    except requests.HTTPError as exc:
        detail = ""
        if exc.response is not None:
            try:
                detail = exc.response.text[:400]
            except Exception:  # noqa: BLE001
                detail = ""
        return "error", None, _redact(f"{exc} :: {detail}".strip(" :")), api_model
    except Exception as exc:  # noqa: BLE001
        return "error", None, str(exc), api_model
    return "ok", tokens, None, api_model


def count_prompt_tokens_messages(
    provider_id: str,
    api_model: str,
    messages: list[dict[str, str]],
    api_key: str,
) -> tuple[str, int | None, str | None, str]:
    """Count tokens for a chat-message prefix (Test 4)."""
    try:
        if provider_id == "anthropic":
            tokens = _anthropic_count_messages(api_model, messages, api_key)
        elif provider_id == "google":
            tokens, api_model = _gemini_count_messages(api_model, messages, api_key)
        elif provider_id == "openai":
            tokens = _openai_compat_count_messages(
                "https://api.openai.com/v1", api_model, messages, api_key
            )
        elif provider_id == "xai":
            tokens = _openai_compat_count_messages(
                "https://api.x.ai/v1", api_model, messages, api_key
            )
        elif provider_id == "deepseek":
            tokens = _openai_compat_count_messages(
                "https://api.deepseek.com", api_model, messages, api_key
            )
        elif provider_id == "qwen":
            tokens = _openai_compat_count_messages(
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                api_model,
                messages,
                api_key,
            )
        elif provider_id == "aws":
            tokens = _bedrock_count_messages(api_model, messages, api_key)
        else:
            return "unsupported_provider", None, f"provider {provider_id} not implemented", api_model
    except requests.HTTPError as exc:
        detail = ""
        if exc.response is not None:
            try:
                detail = exc.response.text[:400]
            except Exception:  # noqa: BLE001
                detail = ""
        return "error", None, _redact(f"{exc} :: {detail}".strip(" :")), api_model
    except Exception as exc:  # noqa: BLE001
        return "error", None, str(exc), api_model
    return "ok", tokens, None, api_model


def _anthropic_count_text(model: str, text: str, api_key: str) -> int:
    response = requests.post(
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
    response.raise_for_status()
    return int(response.json().get("usage", {}).get("input_tokens", 0))


def _anthropic_count_messages(model: str, messages: list[dict[str, str]], api_key: str) -> int:
    # Anthropic requires alternating roles ending with user for some paths;
    # if the prefix ends on assistant, append a tiny user nudge that we do not
    # bill as content of interest — still counts wrapper on the frozen prefix.
    api_messages = list(messages)
    if api_messages and api_messages[-1]["role"] == "assistant":
        api_messages = api_messages + [{"role": "user", "content": "."}]
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model": model, "max_tokens": 1, "messages": api_messages},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return int(response.json().get("usage", {}).get("input_tokens", 0))


def _openai_compat_count_text(base_url: str, model: str, text: str, api_key: str) -> int:
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": text}],
            "max_tokens": 1,
            "temperature": 0,
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return int(response.json().get("usage", {}).get("prompt_tokens", 0))


def _openai_compat_count_messages(
    base_url: str, model: str, messages: list[dict[str, str]], api_key: str
) -> int:
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": messages,
            "max_tokens": 1,
            "temperature": 0,
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return int(response.json().get("usage", {}).get("prompt_tokens", 0))


def _bedrock_messages(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Map {role, content|text} rows onto Bedrock Converse message shape."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        text = msg.get("content") if "content" in msg else msg.get("text", "")
        out.append({"role": role, "content": [{"text": text}]})
    return out


def _bedrock_converse(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    max_tokens: int = 1,
) -> dict[str, Any]:
    region = bedrock_region()
    response = requests.post(
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
    response.raise_for_status()
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


def _gemini_count_messages(model: str, messages: list[dict[str, str]], api_key: str) -> tuple[int, str]:
    contents = []
    for turn in messages:
        role = "user" if turn["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": turn["text"]}]})
    candidates = _gemini_candidates(api_key, "flagship", preferred=model)
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
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:countTokens",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json={"contents": contents},
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code in (404, 400):
        # Fall back to generateContent with maxOutputTokens=1.
        gen = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json={
                "contents": contents,
                "generationConfig": {"maxOutputTokens": 1, "temperature": 0},
            },
            timeout=TIMEOUT_SECONDS,
        )
        gen.raise_for_status()
        return int(gen.json().get("usageMetadata", {}).get("promptTokenCount", 0))
    response.raise_for_status()
    payload = response.json()
    return int(payload.get("totalTokens") or payload.get("promptTokenCount") or 0)


def _gemini_candidates(api_key: str, tier_hint: str, preferred: str) -> list[str]:
    response = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": api_key},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
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
