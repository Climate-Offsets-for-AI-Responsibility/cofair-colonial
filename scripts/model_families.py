"""Provider-aware flagship/workhorse roles and current-version ranking."""
from __future__ import annotations

import re
from datetime import date

UNSTABLE_HINTS = (
    "preview",
    "beta",
    "experimental",
    "expires-on",
    "expires_on",
    "deprecated",
    "retired",
    "legacy",
)
NON_TEXT_HINTS = ("image", "vision", "audio", "live-api")
PRICING_VARIANT_HINTS = ("discount", "starting-", "-through-")
MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        1,
    )
}


def is_ephemeral_model_id(model_id: str) -> bool:
    """True for time-boxed beta aliases such as ``…-expires-on-0910``."""
    name = (model_id or "").lower()
    return any(hint in name for hint in UNSTABLE_HINTS)


def _future_effective_date(text: str) -> date | None:
    normalized = re.sub(r"[_\s]+", "-", (text or "").lower())
    match = re.search(
        r"(?:starting|effective)-([a-z]+)-(\d{1,2})-(\d{4})",
        normalized,
    )
    if not match or match.group(1) not in MONTHS:
        return None
    return date(int(match.group(3)), MONTHS[match.group(1)], int(match.group(2)))


def is_eligible_model(
    provider_id: str,
    model_id: str,
    display_name: str | None = None,
    as_of: date | None = None,
) -> bool:
    """Whether a catalog row can represent a stable current text model."""
    del provider_id  # eligibility vocabulary is shared; roles remain provider-specific
    text = f"{model_id} {display_name or ''}".lower()
    if any(
        hint in text
        for hint in (*UNSTABLE_HINTS, *NON_TEXT_HINTS, *PRICING_VARIANT_HINTS)
    ):
        return False
    if " through " in text:
        return False
    effective = _future_effective_date(text)
    return effective is None or effective <= (as_of or date.today())


def model_role(provider_id: str, model_id: str) -> str | None:
    """Classify a provider model ID as flagship, workhorse, or untracked."""
    name = (model_id or "").lower()

    if provider_id == "anthropic":
        if "claude-opus-" in name:
            return "flagship"
        if "claude-haiku-" in name:
            return "workhorse"
    elif provider_id == "openai":
        # Within one generation the suffix is the product line, not a version.
        # Astra is the top line; Sol is the prior top line and only wins when
        # no Astra is present. Luna and chat-latest are the workhorse line.
        if re.fullmatch(r"gpt-[\d.]+-astra", name) or re.fullmatch(r"gpt-[\d.]+-sol", name):
            return "flagship"
        if name == "chat-latest" or re.fullmatch(r"gpt-[\d.]+-luna", name):
            return "workhorse"
    elif provider_id == "google":
        if any(hint in name for hint in ("image", "vision", "audio", "live-api", "flash-lite")):
            return None
        # End-anchored: a same-version specialty (`gemini-3.8-flash-cyber`) is
        # not a newer workhorse than `gemini-3.8-flash`.
        if re.fullmatch(r"gemini-[\d.]+-pro", name):
            return "flagship"
        if re.fullmatch(r"gemini-[\d.]+-flash", name):
            return "workhorse"
    elif provider_id == "xai":
        if re.fullmatch(r"grok-build-\d+(?:\.\d+)*", name):
            return "workhorse"
        # Product line only (`grok-4.7`). A dated snapshot such as
        # `grok-4.20-0309-non-reasoning` is not a higher version than 4.6 or
        # 4.7 — the extra segments are a build, not a minor increment.
        if re.fullmatch(r"grok-\d+(?:\.\d+)?", name):
            return "flagship"
    elif provider_id == "aws":
        if name in {"nova-premier", "nova-pro"} or re.match(r"^nova-[\d.]+-pro(?:-|$)", name):
            return "flagship"
        if name in {"nova-micro", "nova-lite"} or re.match(
            r"^nova-[\d.]+-(?:micro|lite)(?:-|$)", name
        ):
            return "workhorse"
    elif provider_id == "deepseek":
        if name == "deepseek-flash" or re.match(r"^deepseek-.*-flash(?:-|$)", name):
            return "workhorse"
        if re.match(r"^deepseek-.*-pro(?:-|$)", name):
            return "flagship"
    elif provider_id == "qwen":
        if re.match(r"^qwen[\d.]*-max(?:-|$)", name):
            return "flagship"
        if re.match(r"^qwen[\d.]*-flash(?:-|$)", name):
            return "workhorse"
    return None


def model_version_key(model_id: str) -> tuple[int, ...]:
    """Sort key so ``v4.1`` outranks ``v4`` and ``v3``."""
    if (model_id or "").lower() in {"chat-latest", "deepseek-flash", "qwen-flash"}:
        return (10**9,)
    parts = [int(part) for part in re.findall(r"\d+", model_id or "")]
    return tuple(parts) if parts else (0,)


def deepseek_family_role(model_id: str) -> str | None:
    """Map a DeepSeek id onto the /tokens panel role, or None if it is not a sentinel."""
    if not is_eligible_model("deepseek", model_id):
        return None
    return model_role("deepseek", model_id)


def openai_brand_rank(model_id: str) -> int:
    """Astra outranks Sol, which outranks Luna, inside the same generation."""
    match = re.fullmatch(r"gpt-[\d.]+-(astra|sol|luna)", (model_id or "").lower())
    if not match:
        return 0
    return {"astra": 3, "sol": 2, "luna": 1}[match.group(1)]


def rank_ids_for_tier(
    provider_id: str,
    tier: str,
    model_ids: list[str],
    as_of: date | None = None,
) -> list[str]:
    """Newest stable matching family member first for every tracked provider."""
    matching = [
        model_id
        for model_id in model_ids
        if model_role(provider_id, model_id) == tier
        and is_eligible_model(provider_id, model_id, as_of=as_of)
    ]
    # Higher product version first. On a tie, the shorter id wins so a
    # suffixed variant cannot outrank the canonical model of the same version.
    matching.sort(
        key=lambda model_id: (
            model_version_key(model_id),
            openai_brand_rank(model_id) if provider_id == "openai" else 0,
            -model_id.count("-"),
            -len(model_id),
        ),
        reverse=True,
    )
    return matching
