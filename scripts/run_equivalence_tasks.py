#!/usr/bin/env python3
"""Run the daily token-equivalence tasks across configured providers.

Writes dashboard/data/equivalence_runs.json with one row per
(run_date, task_id, provider_id, tier, mode).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import requests
from dotenv import load_dotenv

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from provider_token_count import (  # noqa: E402
    _is_model_unavailable,
    _status_for_http_error,
    api_model_candidates,
    bedrock_region,
    env_for_provider,
    openai_compatible_body,
)
from provider_http import request_with_retry
from task_corpus import (  # noqa: E402
    CHAT_CORPUS_VERSION,
    CORPUS_VERSION,
    E_USER_PROMPTS,
    METER_TASK_IDS,
    OUTPUT_POLICY_VERSION,
    TASK_PROMPTS,
    TASK_SPECS,
)
from cost_events import (  # noqa: E402
    build_cost_event,
    load_cost_events,
    merge_cost_events,
    save_cost_events,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SHARED_ENV = (REPO_ROOT.parent / "cofair" / ".env" / ".env.cofair")
load_dotenv()
if DEFAULT_SHARED_ENV.exists():
    load_dotenv(DEFAULT_SHARED_ENV, override=False)
DATA_DIR = REPO_ROOT / "dashboard" / "data"
EQUIVALENCE_FILE = DATA_DIR / "equivalence.json"
RUNS_FILE = DATA_DIR / "equivalence_runs.json"

TIMEOUT_SECONDS = 90

_GOOGLE_MODELS_CACHE: list[str] | None = None


def _http_request(method: str, url: str, **kwargs):
    """Thin wrapper so unit tests can patch HTTP without touching provider_http."""
    return request_with_retry(method, url, **kwargs)


def now_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def current_run_date(today: date | None = None) -> str:
    """The UTC day this run belongs to.

    The meter used to snap to the ISO week's Monday. On a daily cadence that
    anchor is actively harmful: rows are replaced on a key that includes it, so
    every run in a week would overwrite the last and the record would still show
    one observation per week without ever saying so.
    """
    return (today or datetime.now(timezone.utc).date()).isoformat()


class Usage(NamedTuple):
    """One call's billed quantities, plus whether the provider cut it short.

    `truncated` is read from the provider's own stop reason rather than inferred
    from `tokens_out == max_tokens`. The inference was only ever a proxy, and it
    stops working entirely once there is no cap to compare against (policy 4.0.0).
    """

    tokens_in: int
    tokens_out: int
    truncated: bool
    # The cap actually sent, or None if the parameter was omitted. Recorded so a
    # row stays interpretable after the policy changes again.
    cap_sent: int | None
    assistant_text: str = ""


# Anthropic's Messages API requires `max_tokens`, so "uncapped" is not expressible
# there and some number has to be sent. The goal is a number that will not bind
# even on a model that has not shipped yet, which rules out writing one down: a
# hard-coded limit is wrong in both directions, 400-ing the provider when a model's
# maximum is lower and silently censoring when it is higher.
#
# So ask for more than any model could allow and let the API name the real limit.
# The top rung is deliberately absurd; the normal path is one rejection whose
# message states the maximum ("max_tokens: 1000000 > 64000, which is the maximum
# allowed..."), which is then used exactly. Rejected requests are not billed, so
# discovery costs latency only, and it tracks a limit that moves in either
# direction without anyone editing this file.
#
# The ladder is the fallback for when that message cannot be parsed — including the
# case where a large `max_tokens` is refused because the request would have to be
# streamed. Stepping down is the right response to that too, since these calls are
# not streamed.
ANTHROPIC_MAX_TOKENS_LADDER = (
    1_000_000,
    262_144,
    131_072,
    64_000,
    32_000,
    16_384,
    8_192,
    4_096,
)

# Highest value a model has accepted this run, and the lowest it has refused.
# Process-local on purpose: every run re-derives the limit from the live API, so a
# model whose maximum *rises* is picked up the next morning without intervention.
_ANTHROPIC_ACCEPTED_MAX: dict[str, int] = {}
_ANTHROPIC_REJECTED_MIN: dict[str, int] = {}

# "max_tokens: 1000000 > 64000, which is the maximum allowed number of output
# tokens for claude-..." — the second number is the model's real limit.
_ANTHROPIC_LIMIT_RE = re.compile(r"max_tokens:\s*\d+\s*>\s*(\d+)")


def _as_messages(prompt_or_messages: str | list[dict[str, str]]) -> list[dict[str, str]]:
    if isinstance(prompt_or_messages, str):
        return [{"role": "user", "content": prompt_or_messages}]
    return list(prompt_or_messages)


def _messages_for_provider(
    provider_id: str, messages: list[dict[str, str]]
) -> list[dict[str, Any]]:
    if provider_id == "google":
        return [
            {
                "role": "model" if item["role"] == "assistant" else "user",
                "parts": [{"text": item["content"]}],
            }
            for item in messages
        ]
    if provider_id == "aws":
        return [
            {"role": item["role"], "content": [{"text": item["content"]}]}
            for item in messages
        ]
    return [dict(item) for item in messages]


def _openai_assistant_text(payload: dict) -> str:
    choices = payload.get("choices") or [{}]
    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _redact_secrets(text: str) -> str:
    redacted = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    redacted = re.sub(r"(?i)(x-api-key\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", redacted)
    redacted = re.sub(r'(?i)("api[_-]?key"\s*:\s*")[^"]+(")', r"\1[REDACTED]\2", redacted)
    redacted = re.sub(r'(?i)("token"\s*:\s*")[^"]+(")', r"\1[REDACTED]\2", redacted)
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|token|key)\s*[:=]\s*[^\s,;]+",
        lambda m: f"{m.group(1)}=[REDACTED]",
        redacted,
    )
    redacted = re.sub(r"\bsk-[A-Za-z0-9._-]+\b", "[REDACTED]", redacted)
    redacted = re.sub(r"\bAIza[0-9A-Za-z\-_]{20,}\b", "[REDACTED]", redacted)
    redacted = re.sub(r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED]", redacted)
    redacted = re.sub(
        r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9._-]{2,}\.[A-Za-z0-9._-]{2,}\b",
        "[REDACTED]",
        redacted,
    )
    return redacted


def _anthropic_call(model: str, messages: list[dict[str, str]], max_tokens: int, api_key: str):
    return _http_request(
        "POST",
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        },
        timeout=TIMEOUT_SECONDS,
    )


def _is_max_tokens_rejection(response) -> bool:
    """A 400 specifically about `max_tokens` exceeding the model's limit.

    Deliberately narrow: any other 400 is a real fault and must surface rather
    than be retried down the ladder and reported as a smaller cap.
    """
    if response is None or response.status_code != 400:
        return False
    try:
        body = response.text.lower()
    except Exception:  # noqa: BLE001
        return False
    return "max_tokens" in body


def _anthropic_stated_limit(response) -> int | None:
    """The maximum the rejection names, if it names one."""
    try:
        match = _ANTHROPIC_LIMIT_RE.search(response.text)
    except Exception:  # noqa: BLE001
        return None
    return int(match.group(1)) if match else None


def _cap_below(model: str, value: int) -> int | None:
    """Next rung down, never at or above a value this model has refused."""
    ceiling = min(value, _ANTHROPIC_REJECTED_MIN.get(model, value))
    rungs = [rung for rung in ANTHROPIC_MAX_TOKENS_LADDER if rung < ceiling]
    return max(rungs) if rungs else None


def _cap_above(model: str, value: int) -> int | None:
    """Next rung up, staying strictly below anything this model has refused."""
    limit = _ANTHROPIC_REJECTED_MIN.get(model)
    rungs = [
        rung
        for rung in ANTHROPIC_MAX_TOKENS_LADDER
        if rung > value and (limit is None or rung < limit)
    ]
    return min(rungs) if rungs else None


def _initial_cap(model: str) -> int | None:
    """Where to start: what this model already accepted, else the top rung."""
    accepted = _ANTHROPIC_ACCEPTED_MAX.get(model)
    if accepted is not None:
        return accepted
    return _cap_below(model, ANTHROPIC_MAX_TOKENS_LADDER[0] + 1)


def _usage_from_anthropic(payload: dict, cap_sent: int) -> Usage:
    usage = payload.get("usage", {})
    assistant_text = "".join(
        block.get("text", "")
        for block in payload.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    return Usage(
        int(usage.get("input_tokens", 0)),
        int(usage.get("output_tokens", 0)),
        payload.get("stop_reason") == "max_tokens",
        cap_sent,
        assistant_text,
    )


def run_anthropic(
    model: str,
    prompt_or_messages: str | list[dict[str, str]],
    max_tokens: int | None,
    api_key: str,
) -> Usage:
    messages = _as_messages(prompt_or_messages)
    if max_tokens is not None:
        # An explicit cap is a caller's instruction, not a guess to be revised.
        response = _anthropic_call(model, messages, max_tokens, api_key)
        response.raise_for_status()
        return _usage_from_anthropic(response.json(), max_tokens)

    candidate = _initial_cap(model)
    tried: set[int] = set()
    last_response = None

    while candidate is not None and candidate not in tried:
        tried.add(candidate)
        last_response = response = _anthropic_call(model, messages, candidate, api_key)

        if _is_max_tokens_rejection(response):
            stated = _anthropic_stated_limit(response)
            _ANTHROPIC_REJECTED_MIN[model] = min(
                candidate, _ANTHROPIC_REJECTED_MIN.get(model, candidate)
            )
            if stated is not None:
                # The API named the limit, so stop guessing at rungs. Recording
                # stated+1 as refused also means a later truncation at `stated`
                # is understood as the model's own ceiling rather than ours.
                _ANTHROPIC_REJECTED_MIN[model] = stated + 1
                candidate = stated
            else:
                candidate = _cap_below(model, candidate)
            continue

        response.raise_for_status()
        usage = _usage_from_anthropic(response.json(), candidate)
        _ANTHROPIC_ACCEPTED_MAX[model] = max(
            candidate, _ANTHROPIC_ACCEPTED_MAX.get(model, candidate)
        )

        if not usage.truncated:
            return usage

        # Truncated, so this is a floor on the model's length rather than a
        # measurement of it. Step back *up* the ladder if there is headroom below
        # what the model has refused. This is what stops the cache from pinning
        # the instrument: a cap discovered once is only a hint, and a truncated
        # reading is proof the hint was too low.
        higher = _cap_above(model, candidate)
        if higher is None:
            # Nothing left to try, so the ceiling is the provider's own and
            # `truncated` reports it honestly rather than hiding it.
            return usage
        candidate = higher

    # Every candidate refused: surface it rather than returning a zeroed reading.
    if last_response is not None:
        last_response.raise_for_status()
    raise RuntimeError(f"anthropic rejected every max_tokens candidate for {model}")


def run_openai_compatible(
    base_url: str,
    model: str,
    prompt_or_messages: str | list[dict[str, str]],
    max_tokens: int | None,
    api_key: str,
) -> Usage:
    messages = _as_messages(prompt_or_messages)
    response = _http_request(
        "POST",
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=openai_compatible_body(base_url, model, messages, max_tokens),
        timeout=TIMEOUT_SECONDS,
    )
    if not response.ok:
        response.raise_for_status()
    payload = response.json()
    usage = payload.get("usage", {})
    choices = payload.get("choices") or [{}]
    return Usage(
        int(usage.get("prompt_tokens", 0)),
        int(usage.get("completion_tokens", 0)),
        choices[0].get("finish_reason") == "length",
        max_tokens,
        _openai_assistant_text(payload),
    )


def run_gemini(
    model: str,
    prompt_or_messages: str | list[dict[str, str]],
    max_tokens: int | None,
    api_key: str,
) -> Usage:
    messages = _as_messages(prompt_or_messages)
    contents = _messages_for_provider("google", messages)
    generation_config: dict = {"temperature": 0}
    # Omitted entirely when uncapped, so the model applies its own maximum.
    if max_tokens is not None:
        generation_config["maxOutputTokens"] = max_tokens
    response = _http_request(
        "POST",
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json={
            "contents": contents,
            "generationConfig": generation_config,
        },
        timeout=TIMEOUT_SECONDS,
    )
    if not response.ok:
        response.raise_for_status()
    payload = response.json()
    usage = payload.get("usageMetadata", {})
    candidates = payload.get("candidates") or [{}]
    assistant_text = "".join(
        part.get("text", "")
        for part in candidates[0].get("content", {}).get("parts", [])
        if isinstance(part, dict)
    )
    return Usage(
        int(usage.get("promptTokenCount", 0)),
        int(usage.get("candidatesTokenCount", 0)),
        candidates[0].get("finishReason") == "MAX_TOKENS",
        max_tokens,
        assistant_text,
    )


def run_bedrock(
    model: str,
    prompt_or_messages: str | list[dict[str, str]],
    max_tokens: int | None,
    api_key: str,
) -> Usage:
    messages = _as_messages(prompt_or_messages)
    body_messages = _messages_for_provider("aws", messages)
    region = bedrock_region()
    inference_config: dict = {"temperature": 0}
    if max_tokens is not None:
        inference_config["maxTokens"] = max_tokens
    response = _http_request(
        "POST",
        f"https://bedrock-runtime.{region}.amazonaws.com/model/{model}/converse",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "messages": body_messages,
            "inferenceConfig": inference_config,
        },
        timeout=TIMEOUT_SECONDS,
    )
    if not response.ok:
        response.raise_for_status()
    payload = response.json()
    usage = payload.get("usage", {})
    assistant_text = "".join(
        block.get("text", "")
        for block in payload.get("output", {}).get("message", {}).get("content", [])
        if isinstance(block, dict)
    )
    return Usage(
        int(usage.get("inputTokens", 0)),
        int(usage.get("outputTokens", 0)),
        payload.get("stopReason") == "max_tokens",
        max_tokens,
        assistant_text,
    )


def available_google_models(api_key: str) -> list[str]:
    global _GOOGLE_MODELS_CACHE
    if _GOOGLE_MODELS_CACHE is not None:
        return _GOOGLE_MODELS_CACHE
    response = _http_request(
        "GET",
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": api_key},
        timeout=TIMEOUT_SECONDS,
    )
    if not response.ok:
        response.raise_for_status()
    payload = response.json()
    models = []
    for item in payload.get("models", []):
        methods = item.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        name = item.get("name", "")
        if not name.startswith("models/"):
            continue
        models.append(name.replace("models/", ""))
    _GOOGLE_MODELS_CACHE = models
    return models


def google_candidates_for_tier(tier: str, api_key: str) -> list[str]:
    models = available_google_models(api_key)
    models = [
        model
        for model in models
        if "gemini" in model
        and "tts" not in model
        and "embedding" not in model
        and "aqa" not in model
        and "vision" not in model
        and "imagen" not in model
    ]
    ranked = []
    if tier == "flagship":
        ranked.extend([m for m in models if "pro" in m])
    ranked.extend([m for m in models if "flash" in m])
    ranked.extend([m for m in models if m not in ranked])
    if not ranked:
        ranked = ["gemini-2.0-flash"]
    return ranked


OPENAI_COMPATIBLE_BASES = {
    "openai": "https://api.openai.com/v1",
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}


def tier_candidates(
    provider_id: str,
    model_id: str,
    tier: str,
    api_key: str | None,
    fallback_model_ids: list[str] | None = None,
) -> list[str]:
    """Ordered api model ids to try for this panel row.

    Google is resolved from the account's live model list; every other provider
    walks its own pinned id first, then the rest of the tier's preference list.
    """
    if provider_id == "google" and api_key:
        return google_candidates_for_tier(tier, api_key)
    return api_model_candidates(provider_id, model_id, tier, api_key, fallback_model_ids)


def resolve_api_model(provider_id: str, model_id: str, tier: str, api_key: str | None = None) -> str:
    candidates = tier_candidates(provider_id, model_id, tier, api_key)
    return candidates[0] if candidates else model_id


def candidate_plan(
    provider_id: str, tier: str, entry: dict, api_key: str | None
) -> list[tuple[str, float | None, float | None]]:
    """Preference-ordered (api_model, input_price, output_price) for a panel row.

    Prices ride along so a row that falls through to a different model is valued
    at that model's rate card rather than the pinned one's.
    """
    own = (entry.get("input_price"), entry.get("output_price"))
    if provider_id == "google" and api_key:
        return [(m, *own) for m in google_candidates_for_tier(tier, api_key)]

    plan: list[tuple[str, float | None, float | None]] = []
    seen: set[str] = set()
    for row in [entry, *(entry.get("api_candidates") or [])]:
        prices = (row.get("input_price"), row.get("output_price"))
        for api_model in api_model_candidates(provider_id, row["model_id"], tier, api_key):
            if api_model in seen:
                continue
            seen.add(api_model)
            plan.append((api_model, *prices))
    return plan


def _run_one(
    provider_id: str, api_model: str, prompt: str, max_tokens: int | None, api_key: str
) -> Usage:
    return _run_messages(
        provider_id,
        api_model,
        [{"role": "user", "content": prompt}],
        max_tokens,
        api_key,
    )


def _run_messages(
    provider_id: str,
    api_model: str,
    messages: list[dict[str, str]],
    max_tokens: int | None,
    api_key: str,
) -> Usage:
    if provider_id == "anthropic":
        return run_anthropic(api_model, messages, max_tokens, api_key)
    if provider_id == "google":
        return run_gemini(api_model, messages, max_tokens, api_key)
    if provider_id in OPENAI_COMPATIBLE_BASES:
        return run_openai_compatible(
            OPENAI_COMPATIBLE_BASES[provider_id], api_model, messages, max_tokens, api_key
        )
    if provider_id == "aws":
        return run_bedrock(api_model, messages, max_tokens, api_key)
    raise LookupError(f"provider {provider_id} not implemented")


class TaskResult(NamedTuple):
    status: str
    tokens_in: int | None
    tokens_out: int | None
    error: str | None
    api_model: str
    input_price: float | None
    output_price: float | None
    # From the provider's stop reason, not from comparing against a cap.
    truncated: bool = False
    cap_sent: int | None = None


class TurnResult(NamedTuple):
    turn: int
    usage: Usage
    messages: list[dict[str, Any]]
    input_price: float | None
    output_price: float | None
    api_model: str


def _http_error_detail(exc: requests.HTTPError) -> str:
    detail = None
    if exc.response is not None:
        try:
            detail = exc.response.text[:500]
        except Exception:  # noqa: BLE001
            detail = None
    return _redact_secrets(f"{exc} :: {detail}" if detail else f"{exc}")


def run_provider_task(
    entry: dict, task_id: str, max_tokens: int | None, dry_run: bool
) -> TaskResult:
    provider_id = entry["provider_id"]
    model_id = entry["model_id"]
    tier = entry["tier"]
    prompt = TASK_PROMPTS[task_id]
    own_prices = (entry.get("input_price"), entry.get("output_price"))

    if dry_run:
        api_model = resolve_api_model(provider_id, model_id, tier, api_key=None)
        return TaskResult("dry_run", None, None, None, api_model, *own_prices)

    api_key = env_for_provider(provider_id)
    if not api_key:
        api_model = resolve_api_model(provider_id, model_id, tier, api_key=None)
        return TaskResult(
            "missing_key", None, None, "provider API key missing", api_model, *own_prices
        )

    plan = candidate_plan(provider_id, tier, entry, api_key)
    api_model = plan[0][0] if plan else model_id
    last_error: str | None = None
    for candidate, input_price, output_price in plan:
        api_model = candidate
        try:
            usage = _run_one(provider_id, candidate, prompt, max_tokens, api_key)
        except LookupError as exc:
            return TaskResult(
                "unsupported_provider", None, None, str(exc), candidate, *own_prices
            )
        except requests.HTTPError as exc:
            last_error = _http_error_detail(exc)
            # A pinned id can be absent from the catalog, Legacy on this key, or
            # missing in the region; those justify the next candidate. Anything
            # else is a real fault and must surface on the first attempt.
            if _is_model_unavailable(exc):
                continue
            return TaskResult(_status_for_http_error(exc), None, None, last_error, candidate, *own_prices)
        except Exception as exc:  # noqa: BLE001
            return TaskResult("error", None, None, _redact_secrets(str(exc)), candidate, *own_prices)
        return TaskResult(
            "ok",
            usage.tokens_in,
            usage.tokens_out,
            None,
            candidate,
            input_price,
            output_price,
            usage.truncated,
            usage.cap_sent,
        )

    return TaskResult(
        "error",
        None,
        None,
        last_error or "no callable model candidate",
        api_model,
        *own_prices,
    )


def run_provider_conversation(
    entry: dict, max_tokens: int | None, dry_run: bool
) -> tuple[TaskResult, list[TurnResult]]:
    provider_id = entry["provider_id"]
    model_id = entry["model_id"]
    tier = entry["tier"]
    own_prices = (entry.get("input_price"), entry.get("output_price"))

    if dry_run:
        api_model = resolve_api_model(provider_id, model_id, tier, api_key=None)
        return TaskResult("dry_run", None, None, None, api_model, *own_prices), []

    api_key = env_for_provider(provider_id)
    if not api_key:
        api_model = resolve_api_model(provider_id, model_id, tier, api_key=None)
        return (
            TaskResult(
                "missing_key", None, None, "provider API key missing", api_model, *own_prices
            ),
            [],
        )

    plan = candidate_plan(provider_id, tier, entry, api_key)
    api_model = plan[0][0] if plan else model_id
    last_error: str | None = None
    successful_turns: list[TurnResult] = []
    for candidate, input_price, output_price in plan:
        api_model = candidate
        conversation: list[dict[str, str]] = []
        candidate_turns: list[TurnResult] = []
        total_in = 0
        total_out = 0
        truncated = False
        cap_sent = max_tokens
        try:
            for turn_index, prompt in enumerate(E_USER_PROMPTS, start=1):
                conversation.append({"role": "user", "content": prompt})
                usage = _run_messages(provider_id, candidate, conversation, max_tokens, api_key)
                sent_messages = _messages_for_provider(provider_id, conversation)
                turn_result = TurnResult(
                    turn_index,
                    usage,
                    sent_messages,
                    input_price,
                    output_price,
                    candidate,
                )
                candidate_turns.append(turn_result)
                successful_turns.append(turn_result)
                total_in += usage.tokens_in
                total_out += usage.tokens_out
                truncated = truncated or usage.truncated
                cap_sent = usage.cap_sent
                if usage.assistant_text.strip() == "":
                    raise ValueError("empty assistant output")
                conversation.append({"role": "assistant", "content": usage.assistant_text})
        except LookupError as exc:
            return (
                TaskResult("unsupported_provider", None, None, str(exc), candidate, *own_prices),
                successful_turns,
            )
        except requests.HTTPError as exc:
            last_error = _http_error_detail(exc)
            if _is_model_unavailable(exc):
                continue
            return (
                TaskResult(
                    _status_for_http_error(exc),
                    None,
                    None,
                    last_error,
                    candidate,
                    *own_prices,
                ),
                successful_turns,
            )
        except Exception as exc:  # noqa: BLE001
            return (
                TaskResult("error", None, None, _redact_secrets(str(exc)), candidate, *own_prices),
                successful_turns,
            )

        return (
            TaskResult(
                "ok",
                total_in,
                total_out,
                None,
                candidate,
                input_price,
                output_price,
                truncated,
                cap_sent,
            ),
            successful_turns,
        )

    return (
        TaskResult(
            "error",
            None,
            None,
            last_error or "no callable model candidate",
            api_model,
            *own_prices,
        ),
        successful_turns,
    )


def usd_value(tokens_in: int | None, tokens_out: int | None, input_price: float, output_price: float) -> float | None:
    if tokens_in is None or tokens_out is None:
        return None
    return (tokens_in / 1_000_000) * input_price + (tokens_out / 1_000_000) * output_price


def implied_per_million(tokens_in: int | None, tokens_out: int | None, usd: float | None) -> float | None:
    if usd is None or tokens_in is None or tokens_out is None:
        return None
    total = tokens_in + tokens_out
    if total <= 0:
        return None
    return usd * (1_000_000 / total)


def _text_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_text_chars(item) for item in value)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return len(value["text"])
        total = 0
        if "content" in value:
            total += _text_chars(value["content"])
        if "parts" in value:
            total += _text_chars(value["parts"])
        return total
    return 0


def _turn_message_chars(messages: list[dict[str, Any]]) -> int:
    return sum(_text_chars(message) for message in messages)


def _run_id_model_key(api_model: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", api_model).strip("-").lower() or "model"


def load_equivalence() -> dict:
    return json.loads(EQUIVALENCE_FILE.read_text())


def load_existing_runs() -> list[dict]:
    if not RUNS_FILE.exists():
        return []
    payload = json.loads(RUNS_FILE.read_text())
    return payload.get("rows", [])


def save_runs(rows: list[dict]) -> None:
    RUNS_FILE.write_text(
        json.dumps(
            {
                "generated_at": now_iso_z(),
                "row_count": len(rows),
                "rows": rows,
            },
            indent=2,
        )
        + "\n"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Run the daily token-equivalence tasks.")
    ap.add_argument("--mode", choices=["two", "three"], default="two")
    ap.add_argument("--date", help="Override the run date (YYYY-MM-DD); defaults to today UTC")
    ap.add_argument("--dry-run", action="store_true", help="Do not call providers; emit dry_run statuses.")
    ap.add_argument("--limit-models", type=int, default=0, help="Optional model row limit for smoke runs.")
    ap.add_argument(
        # One a day, the same as the flagship. The default was 3 under the
        # three-replicate regime and the scheduled workflow has passed 1
        # explicitly ever since; leaving the old default in place tripled the
        # workhorse spend of any run started by hand or by a runbook that
        # forgot the flag, and did it invisibly, because replicates are
        # collapsed to a median before anything is charted.
        "--workhorse-replicates",
        type=int,
        default=1,
        help="Replicates for workhorse tier (flagship always 1). Default 1.",
    )
    ap.add_argument(
        "--provider",
        choices=["anthropic", "openai", "google", "xai", "aws", "deepseek", "qwen"],
        help="Run for one provider only.",
    )
    return ap


def main() -> int:
    args = build_arg_parser().parse_args()

    eq = load_equivalence()
    tasks = {task["task_id"]: task for task in eq["tasks"]}
    pricing_snapshot_date = eq.get("pricing_snapshot_date")
    models = list(eq["selected_models_by_mode"][args.mode])
    if args.provider:
        models = [model for model in models if model["provider_id"] == args.provider]
    if args.limit_models and args.limit_models > 0:
        models = models[: args.limit_models]

    run_date = args.date or current_run_date()
    existing = load_existing_runs()
    keep = []
    replace_keys = set()
    wh_reps = max(1, args.workhorse_replicates)

    new_rows = []
    incoming_cost_events: list[dict] = []
    executed_meter_keys: set[tuple[str, str, str, str, int]] = set()
    for model in models:
        replicates = wh_reps if model["tier"] == "workhorse" else 1
        for replicate in range(1, replicates + 1):
            for task_id in METER_TASK_IDS:
                task = tasks[task_id]
                # None under output policy 4.0.0 — uncapped. `or` would swallow a
                # legitimate 0 and a legitimate None alike, so be explicit.
                requested_cap = task.get("output_cap")
                if requested_cap is None:
                    requested_cap = task.get("output_tokens")
                output_cap = None if requested_cap is None else int(requested_cap)
                if task_id == "E":
                    result, turns = run_provider_conversation(model, output_cap, args.dry_run)
                else:
                    result = run_provider_task(model, task_id, output_cap, args.dry_run)
                    turns = []
                status = result.status
                tokens_in, tokens_out = result.tokens_in, result.tokens_out
                error, used_model = result.error, result.api_model
                usd = usd_value(tokens_in, tokens_out, result.input_price, result.output_price)
                implied = implied_per_million(tokens_in, tokens_out, usd)
                if task_id == "E" and turns:
                    row_turns = turns[-len(E_USER_PROMPTS) :] if status == "ok" else turns
                    input_chars = sum(_turn_message_chars(turn.messages) for turn in row_turns)
                else:
                    input_chars = len(TASK_PROMPTS[task_id])
                run_at = now_iso_z()
                run_id = f"{run_date}:{args.mode}:{replicate}"

                row = {
                    "run_date": run_date,
                    "mode": args.mode,
                    "task_id": task_id,
                    "provider_id": model["provider_id"],
                    "tier": model["tier"],
                    "replicate": replicate,
                    "model_id": model["model_id"],
                    "api_model": used_model,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    # What was actually sent: the requested cap, or the rung
                    # Anthropic accepted, or None where the parameter was omitted.
                    "output_cap": result.cap_sent if status == "ok" else output_cap,
                    # The provider's own stop reason. Uncapped runs can still be
                    # truncated at a model's internal maximum, and that has to
                    # read as truncation rather than as a natural stopping point.
                    "output_censored": bool(result.truncated),
                    "input_chars": input_chars,
                    "corpus_version": CORPUS_VERSION,
                    "output_policy_version": OUTPUT_POLICY_VERSION,
                    "run_status": status,
                    "error": error,
                    "input_price": result.input_price,
                    "output_price": result.output_price,
                    "usd_value_same_day": usd,
                    "implied_cost_per_1m": implied,
                    "run_at": run_at,
                    "run_id": run_id,
                    "chat_corpus_version": CHAT_CORPUS_VERSION if task_id == "E" else None,
                }
                new_rows.append(row)
                executed_meter_keys.add(
                    (run_date, model["provider_id"], model["tier"], task_id, replicate)
                )
                if task_id == "E":
                    attempt = 0
                    previous_turn = 0
                    attempts: list[int] = []
                    for turn in turns:
                        if turn.turn <= previous_turn:
                            attempt += 1
                        elif attempt == 0:
                            attempt = 1
                        previous_turn = turn.turn
                        attempts.append(attempt)
                    winning_attempt = attempts[-1] if (status == "ok" and attempts) else None
                    for event_index, turn in enumerate(turns, start=1):
                        turn_attempt = attempts[event_index - 1]
                        model_key = _run_id_model_key(turn.api_model)
                        turn_run_id = (
                            f"{run_id}:a{turn_attempt}:{model_key}:t{turn.turn}:n{event_index}"
                        )
                        incoming_cost_events.append(
                            build_cost_event(
                                date=run_date,
                                run_at=run_at,
                                source="meter",
                                provider_id=model["provider_id"],
                                tier=model["tier"],
                                task_id=task_id,
                                turn=turn.turn,
                                request_kind="generation",
                                api_model=turn.api_model,
                                input_tokens=turn.usage.tokens_in,
                                output_tokens=turn.usage.tokens_out,
                                input_price_per_1m=turn.input_price,
                                output_price_per_1m=turn.output_price,
                                pricing_snapshot_date=pricing_snapshot_date,
                                corpus_version=CORPUS_VERSION,
                                chat_corpus_version=CHAT_CORPUS_VERSION,
                                run_id=turn_run_id,
                                replicate=replicate,
                                attempt=turn_attempt,
                                canonical=winning_attempt is not None and turn_attempt == winning_attempt,
                            )
                        )
                elif status == "ok":
                    incoming_cost_events.append(
                        build_cost_event(
                            date=run_date,
                            run_at=run_at,
                            source="meter",
                            provider_id=model["provider_id"],
                            tier=model["tier"],
                            task_id=task_id,
                            turn=None,
                            request_kind="generation",
                            api_model=used_model,
                            input_tokens=tokens_in,
                            output_tokens=tokens_out,
                            input_price_per_1m=result.input_price,
                            output_price_per_1m=result.output_price,
                            pricing_snapshot_date=pricing_snapshot_date,
                            corpus_version=CORPUS_VERSION,
                            chat_corpus_version=None,
                            run_id=run_id,
                            replicate=replicate,
                            attempt=1,
                            canonical=True,
                        )
                    )
                replace_keys.add(
                    (run_date, args.mode, task_id, model["provider_id"], model["tier"], replicate)
                )

    for row in existing:
        key = (
            row.get("run_date"),
            row.get("mode"),
            row.get("task_id"),
            row.get("provider_id"),
            row.get("tier"),
            row.get("replicate", 1),
        )
        if key in replace_keys:
            continue
        keep.append(row)

    merged = keep + new_rows
    merged.sort(
        key=lambda r: (
            r.get("run_date") or "",
            r["provider_id"],
            r["tier"],
            r.get("replicate", 1),
            r["task_id"],
        )
    )
    if not args.dry_run:
        save_runs(merged)
        existing_cost_events = load_cost_events()
        keep_cost_events = [
            row
            for row in existing_cost_events
            if not (
                row.get("source") == "meter"
                and (
                    row.get("date"),
                    row.get("provider_id"),
                    row.get("tier"),
                    row.get("task_id"),
                    int(row.get("replicate", 1)),
                )
                in executed_meter_keys
            )
        ]
        merged_cost_events = merge_cost_events(keep_cost_events, incoming_cost_events)
        save_cost_events(merged_cost_events)

    ok = sum(1 for row in new_rows if row["run_status"] == "ok")
    print(
        json.dumps(
            {
                "event": "equivalence_runs_written" if not args.dry_run else "equivalence_runs_dry_run",
                "run_date": run_date,
                "mode": args.mode,
                "workhorse_replicates": wh_reps,
                "dry_run": args.dry_run,
                "rows_written": len(new_rows),
                "ok_rows": ok,
                "cost_events_written": len(incoming_cost_events),
                "output_file": None if args.dry_run else str(RUNS_FILE),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
