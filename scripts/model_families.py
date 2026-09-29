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
        # Sonnet is the step under Opus. Haiku is the floor. Price picks.
        if "claude-sonnet-" in name or "claude-haiku-" in name:
            return "workhorse"
    elif provider_id == "openai":
        # Astra is the top line. Sol is that line when Astra is absent, and the
        # everyday line under Astra when both are priced. Luna and chat-latest
        # are cheaper-line candidates; price decides which one is the workhorse.
        if re.fullmatch(r"gpt-[\d.]+-(?:astra|sol)", name):
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
        # Plus is the step under Max. Flash is the floor. Price picks.
        if re.match(r"^qwen[\d.]*-(?:plus|flash)(?:-|$)", name):
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


def everyday_list_price(rows: list[dict]) -> float | None:
    """Input plus output per 1M tokens on the short-context standard rate.

    A long-context or long-prompt row is a different product. The comparison
    uses the everyday rate, and the lower published sum when a model has no
    short-context marker.
    """
    scored: list[tuple[int, float]] = []
    for row in rows:
        raw_in = row.get("latest_input", row.get("input_price"))
        raw_out = row.get("latest_output", row.get("output_price"))
        if raw_in is None or raw_out is None:
            continue
        text = f"{row.get('context_window') or ''} {row.get('billing_variant') or ''}".lower()
        if "short" in text or "lt-200" in text:
            band = 1
        elif "long" in text or "gte-200" in text:
            band = -1
        else:
            band = 0
        scored.append((band, float(raw_in) + float(raw_out)))
    if not scored:
        return None
    # Prefer the short-context band. Inside a band, the lower sum is the
    # everyday rate rather than a long-prompt surcharge.
    band, _total = max(scored, key=lambda item: (item[0], -item[1]))
    return min(total for item_band, total in scored if item_band == band)


def product_line(provider_id: str, model_id: str) -> str | None:
    """The product line inside a provider, ignoring the generation number."""
    name = (model_id or "").lower()
    if provider_id == "openai":
        if name == "chat-latest":
            return "chat"
        match = re.fullmatch(r"gpt-[\d.]+-(astra|sol|luna)", name)
        return match.group(1) if match else None
    if provider_id == "anthropic":
        for line in ("opus", "sonnet", "haiku"):
            if f"claude-{line}-" in name:
                return line
    if provider_id == "google":
        if re.fullmatch(r"gemini-[\d.]+-pro", name):
            return "pro"
        if re.fullmatch(r"gemini-[\d.]+-flash", name):
            return "flash"
    if provider_id == "xai":
        if re.fullmatch(r"grok-build-\d+(?:\.\d+)*", name):
            return "build"
        if re.fullmatch(r"grok-\d+(?:\.\d+)?", name):
            return "grok"
    if provider_id == "aws":
        if "premier" in name:
            return "premier"
        if re.search(r"(?:^nova-[\d.]+-pro(?:-|$)|(?:^nova-pro$))", name):
            return "pro"
        if "micro" in name:
            return "micro"
        if "lite" in name:
            return "lite"
    if provider_id == "deepseek":
        if "flash" in name:
            return "flash"
        if re.search(r"-pro(?:-|$)", name):
            return "pro"
    if provider_id == "qwen":
        for line in ("max", "plus", "flash"):
            if re.search(rf"(?:^|-){line}(?:-|$)", name):
                return line
    return None


# Sol is the flagship when Astra is absent, and the everyday line under Astra.
STEP_DOWN_LINES = {("openai", "sol")}


def _newer_than(model_id: str, other: str) -> bool:
    return (
        model_version_key(model_id),
        -model_id.count("-"),
        -len(model_id),
    ) > (
        model_version_key(other),
        -other.count("-"),
        -len(other),
    )


def order_workhorses(
    provider_id: str,
    model_ids: list[str],
    prices: dict[str, float],
    flagship_id: str | None,
    as_of: date | None = None,
) -> list[str]:
    """Newest member of each cheaper line, dearest-under-the-flagship first.

    The workhorse is the everyday line: strictly cheaper than the flagship, and
    the closest such line rather than the floor. An alias priced at or above
    the flagship (chat-latest today) cannot take the slot. Lines with no price
    stay eligible so a missing rate does not drop the panel.
    """
    flagship_price = prices.get(flagship_id) if flagship_id else None
    flagship_line = product_line(provider_id, flagship_id) if flagship_id else None
    by_line: dict[str, str] = {}
    for model_id in model_ids:
        if not is_eligible_model(provider_id, model_id, as_of=as_of):
            continue
        role = model_role(provider_id, model_id)
        line = product_line(provider_id, model_id)
        step_down = (provider_id, line) in STEP_DOWN_LINES and line != flagship_line
        if role != "workhorse" and not step_down:
            continue
        if line is None:
            continue
        current = by_line.get(line)
        if current is None or _newer_than(model_id, current):
            by_line[line] = model_id

    def under_flagship(model_id: str) -> bool:
        price = prices.get(model_id)
        if flagship_price is None or price is None:
            return True
        return price < flagship_price

    priced = [model_id for model_id in by_line.values() if under_flagship(model_id)]
    pool = priced or list(by_line.values())

    def sort_key(model_id: str) -> tuple:
        price = prices.get(model_id)
        return (
            price if price is not None else -1.0,
            model_version_key(model_id),
            openai_brand_rank(model_id) if provider_id == "openai" else 0,
            -model_id.count("-"),
            -len(model_id),
        )

    pool.sort(key=sort_key, reverse=True)
    rest = [
        model_id
        for model_id in rank_ids_for_tier(provider_id, "workhorse", model_ids, as_of)
        if model_id not in pool
    ]
    return pool + rest


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
