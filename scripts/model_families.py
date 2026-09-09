"""Stable family roles for catalog ids that iterate (DeepSeek v4 → v4.1, …).

Pinned TIER_CANDIDATES ids go stale the day a vendor ships a new generation.
These helpers pick the current flagship/workhorse from whatever the catalog or
the live /models list actually contains, skipping dated beta aliases.
"""
from __future__ import annotations

import re


def is_ephemeral_model_id(model_id: str) -> bool:
    """True for time-boxed beta aliases such as ``…-expires-on-0910``."""
    name = (model_id or "").lower()
    return "expires-on" in name or "expires_on" in name


def model_version_key(model_id: str) -> tuple[int, ...]:
    """Sort key so ``v4.1`` outranks ``v4`` and ``v3``."""
    parts = [int(part) for part in re.findall(r"\d+", model_id or "")]
    return tuple(parts) if parts else (0,)


def deepseek_family_role(model_id: str) -> str | None:
    """Map a DeepSeek id onto the /tokens panel role, or None if it is not a sentinel."""
    name = (model_id or "").lower()
    if not name.startswith("deepseek-"):
        return None
    if is_ephemeral_model_id(name):
        return None
    if "vision" in name:
        return None
    if "flash" in name:
        return "workhorse"
    if "pro" in name:
        return "flagship"
    return None


def rank_ids_for_tier(provider_id: str, tier: str, model_ids: list[str]) -> list[str]:
    """Newest matching family member first. Other providers keep the given order."""
    if provider_id != "deepseek":
        return list(model_ids)
    matching = [model_id for model_id in model_ids if deepseek_family_role(model_id) == tier]
    matching.sort(key=model_version_key, reverse=True)
    return matching
