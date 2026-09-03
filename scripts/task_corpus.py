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

from corpus_long_natural import LONG_NATURAL_CONTEXT
from corpus_long_packet import LONG_CONTEXT_PACKET

# Bump when any prompt text changes. Older run rows keep their own recorded
# version so a corpus edit never silently rewrites history.
#
# 1.0.0 = tasks A-D.
# 1.1.0 = task F added (long context, natural text). Additive on purpose: no
#         existing prompt changed, so every A-E series continues uninterrupted and
#         no epoch reset is needed. Only the fitted content rate changes basis,
#         which `fit_task_ids` records per row so the discontinuity is visible
#         rather than silent (D78).
# 2.0.0 = task D's prompt replaced. Breaking, and the only breaking corpus change
#         so far: D's filler was one sentence repeated 800 times, so every D
#         observation to date measures the cost of re-merging that phrase and is
#         not comparable with a D observation on real text. The series is not
#         deleted — rows carry the version they were collected under and the
#         chart breaks the line where it changes — but the two halves must not be
#         read across (D79).
# 3.0.0 = task E added as a generated three-turn conversation. Breaking: E was
#         previously a frozen wrapper transcript counted input-only; the new E
#         is generated like A–D and F and belongs in the meter, not the ledger.
CORPUS_VERSION = "3.0.0"

# Versioned separately from the prompt text because it governs a different
# quantity. `max_tokens` cannot affect how a prompt tokenizes, so a cap change
# leaves every input-density series continuous — but it does break `tokens_out`
# and `usd` comparability, and those series must be read as starting fresh here.
#
# 1.0.0 = tight per-task caps (400/600/1500/300), which truncated most flagship
#         answers and made observed verbosity a floor rather than a measurement.
# 2.0.0 = one generous ceiling for every task, 4,000.
# 3.0.0 = ceiling raised to 8,000.
# 4.0.0 = no cap at all (below).
OUTPUT_POLICY_VERSION = "4.0.0"

# No cap. Verbosity is the measurement, and any cap high enough to be safe is also
# high enough to bind eventually — 4,000 pinned both DeepSeek tiers at exactly
# 4,000 on task C, and each raise only moves the number a future chattier model
# will reach. A capped run reports the cap, not the model, so the instrument would
# keep quietly converting "this provider got more verbose" into "no change", on the
# term that is 80–98% of the billed tokens for tasks A–C.
#
# `None` means *omit the parameter*, letting each provider apply its own model
# maximum, rather than encoding a guess about that maximum here. Anthropic's
# Messages API requires `max_tokens`, so the runner discovers an accepted value by
# ladder (see ANTHROPIC_MAX_TOKENS_LADDER) instead of this constant holding a
# number that would 400 the entire provider the day a model's limit moved.
#
# What replaces the cap as a guard is detection, not truncation: the runner reads
# each provider's own stop reason, so a truncated run says so instead of being
# inferred from `tokens_out == cap` — an inference that stops working the moment
# there is no cap to compare against.
OUTPUT_CEILING = None

E_USER_PROMPTS = (
    "An organization uses AI for both routine administrative work and "
    "high-stakes analysis. Explain how it should decide when to use a flagship "
    "model and when to use a workhorse model.",
    "Challenge your recommendation from the perspective of a team that values "
    "reliability more than cost.",
    "Now revise the recommendation to address that challenge while keeping "
    "spending predictable. End with a practical three-step policy.",
)

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
    # D is the largest request in the suite and keeps that role — it is what
    # exercises whatever a provider does with a big payload. What changed is the
    # text: it was `"Policy review context sentence. " * 800`, which is big and
    # nothing else. 800 repetitions present one merge decision amortized over
    # 25,743 characters, so its marginal cost was the cost of re-merging that
    # phrase, and it had to be excluded from the fit (D77). The packet is the same
    # order of magnitude with ordinary variety, so D now contributes to the fit
    # instead of poisoning it.
    #
    # The question asks for a fact stated once, deep in section 6, so D also
    # earns its "needle" label for the first time: retrieval and billing are both
    # properties of how a provider handles a large request, and this measures them
    # on the same call.
    "D": (
        "Use the context below and answer the question in 5 bullets.\n\n"
        "Context:\n" + LONG_CONTEXT_PACKET + "\n\nQuestion: What is the long-context "
        "surcharge threshold on contract NG-4471-B, and which three governance "
        "controls does the packet say most reduce billing surprise?"
    ),
    "E": "\n".join(E_USER_PROMPTS),
    "F": (
        "Use the context below and answer the question in 5 bullets.\n\n"
        "Context:\n" + LONG_NATURAL_CONTEXT + "\n\nQuestion: Which three governance "
        "controls most reduce billing surprise?"
    ),
}


# Share of a prompt's words that are distinct. A tokenizer comparison needs text
# whose vocabulary the tokenizers can actually disagree about: BPE merges diverge
# on rare words, casing, punctuation and digits, and a phrase repeated 800 times
# offers exactly one merge decision amortized over 25,743 characters. Every task
# now scores 0.39-0.96; the old task D scored 0.007, which is what this exists to
# catch.
def lexical_variety(task_id: str) -> float:
    words = TASK_PROMPTS[task_id].split()
    return (len(set(words)) / len(words)) if words else 0.0


# Floor for text allowed to set the fitted content rate in `build_ledger_fits`.
# Kept even though no current task is anywhere near it: the guard is not about
# today's corpus but about the next long task somebody adds by repeating filler
# to reach a length, which is how the old task D came to exist. Well below the
# 0.39 the long packet scores and two orders of magnitude above a loop, so it
# separates "natural language" from "one sentence repeated" without being a knob
# that ordinary corpus edits can trip.
MIN_LEXICAL_VARIETY = 0.15

TASK_LEXICAL_VARIETY = {task_id: lexical_variety(task_id) for task_id in TASK_PROMPTS}

# Every task shares `OUTPUT_CEILING`, which is now `None` — uncapped. Per-task caps
# were tuned to the answer each prompt "should" need, which meant the cap, not the
# model, decided the observed length; a single shared ceiling had the same defect at
# a higher number. `output_cap` is still recorded on every run row, now carrying the
# value actually sent to the provider (`None` where the parameter was omitted), so
# the policy in force for a given row stays readable after the fact.
TASK_SPECS = {
    "A": {
        "label": "Bounded Q&A",
        "probes": "Baseline prompt-side tokenizer density on ordinary prose.",
        "output_cap": OUTPUT_CEILING,
        "cadence": "daily",
    },
    "B": {
        "label": "Summarize frozen essay",
        "probes": "Prose compression; input density on a longer natural-language block.",
        "output_cap": OUTPUT_CEILING,
        "cadence": "daily",
    },
    "C": {
        "label": "Code from frozen spec",
        "probes": (
            "Code/punctuation tokenization, where BPE vocabularies diverge most. "
            "Highest-signal task for cross-provider density differences."
        ),
        "output_cap": OUTPUT_CEILING,
        "cadence": "daily",
    },
    "D": {
        "label": "Long-context needle",
        "probes": (
            "The suite's largest request — exposes long-context billing surcharges "
            "and whether a fact stated once survives a 25K-character payload. Its "
            "context was one sentence repeated 800 times until 3 Sep 2026; it is "
            "now a heterogeneous document, so D sets the top of the character span "
            "and contributes to the fit instead of distorting it (D79)."
        ),
        "output_cap": OUTPUT_CEILING,
        "cadence": "daily",
    },
    "E": {
        "label": "Chat conversation",
        "probes": (
            "Multi-turn relational dialogue — three frozen user turns that "
            "exercise how a provider bills conversational context as turns "
            "accumulate, distinct from the wrapper transcript counted "
            "historically under the same task id."
        ),
        "output_cap": OUTPUT_CEILING,
        "cadence": "daily",
    },
    "F": {
        "label": "Long-context prose",
        "probes": (
            "Long context with ordinary variety — the task that lets the fit "
            "separate fixed request overhead from tokenizer density, because it "
            "supplies a wide character span in text vocabularies can disagree "
            "about."
        ),
        "output_cap": OUTPUT_CEILING,
        "cadence": "daily",
    },
}

TASK_IDS = ("A", "B", "C", "D", "E", "F")
GENERATING_TASK_IDS = TASK_IDS
LEDGER_TASK_IDS = ("A", "B", "C", "D", "F")
METER_TASK_IDS = GENERATING_TASK_IDS

TASK_PACKS = {
    "qa": ["A"],
    "summarize": ["B"],
    "code": ["C"],
    "long": ["D"],
    "chat": ["E"],
    "prose": ["F"],
    "suite": ["A", "B", "C"],
    "suiteLong": list(TASK_IDS),
}


# Tasks whose text cannot support a claim about tokenizer density, whatever their
# length. `build_ledger_fits` prefers the complement of this set when it is large
# enough to fit, so the slope is estimated on text a vocabulary can disagree about,
# and falls back to every task — overhead only — when it is not.
#
# Derived rather than listed, so a corpus edit cannot leave a stale hand-written
# set behind: task D drops out because of what it *is*, and if its filler were ever
# replaced with varied text it would rejoin automatically.
def degenerate_task_ids() -> frozenset[str]:
    return frozenset(
        task_id
        for task_id in METER_TASK_IDS
        if lexical_variety(task_id) < MIN_LEXICAL_VARIETY
    )


DEGENERATE_TASK_IDS = degenerate_task_ids()


# Degeneracy is a property of the text that was actually sent, so it is keyed on
# (task, corpus version) and not on the task id alone. Task D's id did not change
# when its prompt did: every D row collected before corpus 2.0.0 measured
# `"Policy review context sentence. " * 800` and must stay out of the fit, while
# every D row after it measures a heterogeneous document and belongs in it.
#
# Keying on the id alone was briefly true and is now exactly wrong — it would have
# readmitted eleven days of filler-derived counts to the historical fit the moment
# the prompt was replaced, retroactively reintroducing the artifact that D77 was
# raised to remove, and it would have done it silently.
DEGENERATE_BEFORE_VERSION = {"D": (2, 0, 0)}


def _version_tuple(version: str | None) -> tuple[int, ...]:
    """Parse `"1.1.0"`. Unparseable or absent sorts oldest, which is the safe
    direction: an unversioned row predates versioning, so it predates the fix."""
    try:
        return tuple(int(part) for part in (version or "0").split("."))
    except ValueError:
        return (0,)


def is_degenerate(task_id: str | None, corpus_version: str | None) -> bool:
    if task_id in DEGENERATE_TASK_IDS:
        return True
    floor = DEGENERATE_BEFORE_VERSION.get(task_id or "")
    return floor is not None and _version_tuple(corpus_version) < floor


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
                "output_policy_version": OUTPUT_POLICY_VERSION,
            }
        )
    return out


TASK_DEFINITIONS = task_definitions()


# ---- Historical wrapper transcript (pre-generated E) -----------------------
#
# CHAT_TASK and CHAT_TRANSCRIPT are historical-only. They interpret legacy
# wrapper-count rows collected before corpus 3.0.0, when task E was a frozen
# ten-turn transcript counted input-only. The canonical task E is TASK_SPECS["E"]
# with TASK_PROMPTS["E"] built from E_USER_PROMPTS.
#
# Versioned separately from A–D so editing the transcript does not invalidate
# the prose/code density series. Counts are input-only: runners never regenerate
# assistant turns — they only ask the provider how many prompt tokens the
# frozen prefix would bill.
#
# 2.0.0 = task E is now a generated three-turn conversation in TASK_PROMPTS.
#         The transcript below is kept only for interpreting historical wrapper
#         rows collected under the old E semantics.
CHAT_CORPUS_VERSION = "2.0.0"

CHAT_TASK_ID = "E"

CHAT_TASK = {
    "task_id": CHAT_TASK_ID,
    "label": "Chat transcript",
    "probes": (
        "Chat wrapper and history-packing overhead across 10 frozen turns — the "
        "structural tokens a provider adds around multi-turn history, which never "
        "appear in the raw user text."
    ),
    "cadence": "daily",
    "chat_corpus_version": CHAT_CORPUS_VERSION,
}

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


# Same invariant size measures A–D publish, over the full 10-turn transcript, so
# task E can be listed in the corpus table without the dashboard recomputing them.
_CHAT_FULL_TEXT = "\n".join(turn["text"] for turn in CHAT_TRANSCRIPT)
CHAT_TASK["input_chars"] = len(_CHAT_FULL_TEXT)
CHAT_TASK["input_bytes"] = len(_CHAT_FULL_TEXT.encode("utf-8"))
CHAT_TASK["input_words"] = len(_CHAT_FULL_TEXT.split())
