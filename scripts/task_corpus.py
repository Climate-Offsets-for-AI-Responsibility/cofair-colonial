#!/usr/bin/env python3
"""Frozen task corpus for the token-equivalence index.

Single source of truth for both the runner (`run_equivalence_tasks.py`) and the
dashboard artifact builder (`build_dashboard_data.py`). The prompts here are a
**frozen corpus**: changing a prompt's text resets the comparability of every
series that references it, so any edit must bump `corpus_version` and is treated
as the start of a new series rather than a continuation of the old one.

Why characters matter: a token is a different unit of information per provider,
so raw token counts are not comparable across vendors. Character count is the
invariant denominator — `tokens_in / (input_chars / 1000)` gives tokenizer
density, which *is* comparable and is what detects a silent re-tokenization.
"""
from __future__ import annotations

# Bump when any prompt text changes. Older run rows keep their own recorded
# version so a corpus edit never silently rewrites history.
CORPUS_VERSION = "1.0.0"

TASK_PROMPTS = {
    "A": (
        "Answer this clearly in 3 bullet points: "
        "What are the practical tradeoffs between batching model calls and "
        "real-time calls for nonprofit analytics dashboards?"
    ),
    "B": (
        "Summarize the following in 5 bullets and 1-sentence conclusion:\n\n"
        "Community organizations are evaluating AI tools under constrained budgets. "
        "They need predictable costs, transparent data handling, and clear auditability "
        "for how resources are allocated. A useful governance model should expose not only "
        "list prices but also whether tokenization behavior changes over time. The same text "
        "can tokenize differently across providers, and that can change silently. "
        "If program funding depends on token-denominated billing, governance teams need "
        "repeatable benchmarks that compare equivalent workloads on a fixed cadence. "
        "The benchmark should include short Q&A tasks, summarization tasks, coding tasks, "
        "and long-context retrieval tasks. It should report observed tokens in, tokens out, "
        "and the implied dollar value under the current provider rate card."
    ),
    "C": (
        "Write Python code for a function normalize_currency(value: str) -> float that:\n"
        "1) accepts '$1,234.50', '1234.5', or 'USD 1234.50'\n"
        "2) returns a float\n"
        "3) raises ValueError on invalid input\n"
        "Then include three unit tests."
    ),
    "D": (
        "Use the context below and answer the question in 5 bullets.\n\n"
        "Context:\n"
        + ("Policy review context sentence. " * 800)
        + "\n\nQuestion: Which three governance controls most reduce billing surprise?"
    ),
}

# `output_cap` bounds spend. It is recorded on every run row so a run that hits
# the ceiling can be marked censored — a capped output is a truncated
# observation, not a measurement of how verbose the model actually is.
TASK_SPECS = {
    "A": {
        "label": "Bounded Q&A",
        "probes": "Baseline prompt-side tokenizer density on ordinary prose.",
        "output_cap": 400,
        "cadence": "weekly",
    },
    "B": {
        "label": "Summarize frozen essay",
        "probes": "Prose compression; input density on a longer natural-language block.",
        "output_cap": 600,
        "cadence": "weekly",
    },
    "C": {
        "label": "Code from frozen spec",
        "probes": (
            "Code/punctuation tokenization, where BPE vocabularies diverge most. "
            "Highest-signal task for cross-provider density differences."
        ),
        "output_cap": 1500,
        "cadence": "weekly",
    },
    "D": {
        "label": "Long-context needle",
        "probes": (
            "Repetitive long context — exposes vocabulary merge behavior and any "
            "long-context billing surcharge."
        ),
        "output_cap": 300,
        "cadence": "weekly",
    },
}

TASK_PACKS = {
    "qa": ["A"],
    "summarize": ["B"],
    "code": ["C"],
    "long": ["D"],
    "suite": ["A", "B", "C"],
    "suiteLong": ["A", "B", "C", "D"],
}


def task_definitions() -> list[dict]:
    """Task metadata with the invariant size measures the index normalizes on."""
    out = []
    for task_id, spec in TASK_SPECS.items():
        prompt = TASK_PROMPTS[task_id]
        out.append(
            {
                "task_id": task_id,
                "label": spec["label"],
                "probes": spec["probes"],
                "prompt": prompt,
                "input_chars": len(prompt),
                "input_bytes": len(prompt.encode("utf-8")),
                "input_words": len(prompt.split()),
                "output_cap": spec["output_cap"],
                "cadence": spec["cadence"],
                "corpus_version": CORPUS_VERSION,
            }
        )
    return out


TASK_DEFINITIONS = task_definitions()


# ---- Test 4: frozen multi-turn chat (wrapper overhead) ---------------------
#
# Versioned separately from A–D so editing the transcript does not invalidate
# the prose/code density series. Counts are input-only: runners never regenerate
# assistant turns — they only ask the provider how many prompt tokens the
# frozen prefix would bill.

CHAT_CORPUS_VERSION = "1.0.0"

# Each user turn ≈15 words; each assistant turn ≈50 words. Locked forever.
CHAT_TRANSCRIPT: list[dict[str, str]] = [
    {
        "role": "user",
        "text": (
            "We need a clear weekly report showing which AI providers quietly "
            "changed how they bill identical prompts."
        ),
    },
    {
        "role": "assistant",
        "text": (
            "A weekly token equivalence report should freeze a small prompt corpus, "
            "record input and output tokens per provider, and normalize by character "
            "count so different tokenizers stay comparable. Pin exact model ids, "
            "reject silent alias repoints, and flag any run that hits an output cap "
            "as censored rather than complete."
        ),
    },
    {
        "role": "user",
        "text": (
            "How should we treat formatting changes like extra spaces or indented "
            "JSON in those frozen prompts?"
        ),
    },
    {
        "role": "assistant",
        "text": (
            "Keep the primary series on unchanged frozen text so week over week "
            "comparisons stay valid. Run a separate ledger that mutates whitespace "
            "and indentation on purpose, then report sensitivity ratios without "
            "mixing those rows into the main density chart. Version every fixture "
            "explicitly whenever a byte changes."
        ),
    },
    {
        "role": "user",
        "text": (
            "What is the difference between tokenizer drift and the model simply "
            "getting more verbose over time?"
        ),
    },
    {
        "role": "assistant",
        "text": (
            "Tokenizer drift moves input tokens for the same characters on a pinned "
            "model. Verbosity inflation leaves input density flat while output tokens "
            "climb for the same instruction. Price hikes raise dollars per token with "
            "flat density. Publish those as three separate signals so a rate card "
            "change is never confused with a silent re tokenization."
        ),
    },
    {
        "role": "user",
        "text": (
            "Can we trust a single weekly sample when claiming a ten percent "
            "shift in token use?"
        ),
    },
    {
        "role": "assistant",
        "text": (
            "No. One observation cannot separate a modest shift from sampling noise, "
            "especially on stochastic outputs. Keep flagship at one replicate for "
            "cost control, run three replicates on the cheap workhorse tier, and "
            "plot the median. Only large effects should be treated as publishable "
            "claims until the series is longer."
        ),
    },
    {
        "role": "user",
        "text": (
            "Why measure chat history separately from single turn prompts in the "
            "token index?"
        ),
    },
    {
        "role": "assistant",
        "text": (
            "Providers inject structural wrappers around multi turn history that never "
            "appear in the raw user text. Counting cumulative prompt tokens on a frozen "
            "transcript exposes that overhead and any week to week change in how the "
            "API packs roles. Local tokenizer libraries often miss those tags, so the "
            "measurement must use the live count path."
        ),
    },
    {
        "role": "user",
        "text": (
            "Should long repetitive context run every day in the free tokenizer "
            "ledger or only weekly?"
        ),
    },
    {
        "role": "assistant",
        "text": (
            "Run short tasks daily because they are cheap and catch tokenizer changes "
            "quickly. Keep the long repetitive block on the weekly cadence with the "
            "task meter so you do not spend most of the ledger budget re counting the "
            "same twenty five thousand characters every night with little added signal."
        ),
    },
    {
        "role": "user",
        "text": (
            "How do we show donors a provider raised effective cost without "
            "raising sticker price per million tokens?"
        ),
    },
    {
        "role": "assistant",
        "text": (
            "Lead with cost per thousand characters of frozen work. If density rises "
            "while dollars per token stay flat, the provider quietly devalued the "
            "token. If density is flat and dollars per token rise, the hike is honest. "
            "Pair both charts with the pinned api model string so alias swaps cannot "
            "hide inside the story."
        ),
    },
    {
        "role": "user",
        "text": (
            "What happens if an output cap truncates the answer during a weekly "
            "meter run?"
        ),
    },
    {
        "role": "assistant",
        "text": (
            "Mark the row as output censored and exclude truncated verbosity from "
            "claims about how chatty the model is. The cap still protects budget, but "
            "it is not a measurement of natural length. Prefer input side density for "
            "drift evidence because that quantity is deterministic for a frozen prompt "
            "on a pinned model."
        ),
    },
    {
        "role": "user",
        "text": (
            "Which providers need live usage probes because they lack a free local "
            "tokenizer library?"
        ),
    },
    {
        "role": "assistant",
        "text": (
            "OpenAI can use tiktoken offline. Gemini exposes count tokens. Anthropic "
            "packages can lag billing. DeepSeek, Qwen, xAI, and Amazon often need a "
            "one token generation just to read usage. Design the ledger adapters to "
            "prefer count endpoints, then fall back to max tokens equals one without "
            "treating that fallback as a quality sample."
        ),
    },
    {
        "role": "user",
        "text": (
            "Summarize the operating cadence we should commit to for the next quarter "
            "of measurement."
        ),
    },
    {
        "role": "assistant",
        "text": (
            "Daily ledger on short frozen tasks, weekly long context count, weekly "
            "task meter with three workhorse replicates, and weekly wrapper counts on "
            "this transcript through turn ten. Skip multilingual fixtures for now. "
            "Publish only large effects, keep corpus versions explicit, and treat "
            "amazon as display only until the Bedrock count adapter ships."
        ),
    },
]


def transcript_prefix(n_turns: int) -> list[dict[str, str]]:
    """Return the first `n_turns` complete user+assistant exchanges (2n messages)."""
    if n_turns <= 0:
        return []
    n_turns = min(n_turns, 10)
    return list(CHAT_TRANSCRIPT[: n_turns * 2])


def transcript_prefix_text(n_turns: int) -> str:
    """Flatten a prefix to plain text for providers without chat count APIs."""
    parts = []
    for turn in transcript_prefix(n_turns):
        parts.append(f"{turn['role'].upper()}: {turn['text']}")
    return "\n\n".join(parts)


def transcript_prefix_chars(n_turns: int) -> int:
    return sum(len(turn["text"]) for turn in transcript_prefix(n_turns))
