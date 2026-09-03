#!/usr/bin/env python3
"""Unit tests for dashboard artifact normalization."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_dashboard_data
from build_dashboard_data import (
    DASHBOARD_START_DATE,
    MIN_FIT_CHAR_SPAN,
    MIN_FIT_TASKS,
    build_cost_comparison,
    build_costs,
    build_equivalence,
    build_ledger_fits,
    build_models,
    build_token_runs,
    include_dashboard_date,
    normalize_snapshot,
    run_date_of,
)
from cost_events import build_cost_event
from run_equivalence_tasks import current_run_date
from task_corpus import (
    DEGENERATE_TASK_IDS,
    E_USER_PROMPTS,
    GENERATING_TASK_IDS,
    LEDGER_TASK_IDS,
    METER_TASK_IDS,
    MIN_LEXICAL_VARIETY,
    TASK_IDS,
    TASK_PROMPTS,
    lexical_variety,
)

# Fixture dates for anything that passes through the epoch filter are pinned to the
# epoch rather than written down. A hard-coded date silently empties the fixture the
# moment DASHBOARD_START_DATE moves past it, and the failure then looks like the
# behaviour under test broke rather than the fixture aging out.
POST_EPOCH = DASHBOARD_START_DATE
PRE_EPOCH = (date.fromisoformat(DASHBOARD_START_DATE) - timedelta(days=1)).isoformat()


class NormalizeSnapshotTest(unittest.TestCase):
    def test_v2_passes_modality_and_context_window(self) -> None:
        snap = {
            "meta": {"schema_version": "2.0"},
            "pricing": [
                {
                    "pricing_id": "google-gemini-2.5-flash-standard-200k-audio-gemini25",
                    "provider_id": "google",
                    "model_id": "gemini-2.5-flash",
                    "display_name": "Gemini 2.5 Flash",
                    "service_tier": "standard",
                    "context_window": ">200k",
                    "modality": "audio",
                    "category": "gemini_2_5",
                    "input_unit": "per_1M_tokens",
                    "input_price": 1.0,
                    "output_price": None,
                    "output_unit": None,
                    "cache_read_price": None,
                    "currency": "USD",
                    "is_active": True,
                }
            ],
        }
        rows = normalize_snapshot(snap, "2026-08-10")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["modality"], "audio")
        self.assertEqual(rows[0]["context_window"], ">200k")

    def test_v2_keeps_google_text_output_only_rows(self) -> None:
        """Vertex splits modalities: text is often output-only (null input_unit)."""
        snap = {
            "meta": {"schema_version": "2.1.0"},
            "pricing": [
                {
                    "pricing_id": "google-gemini-2.5-flash-standard-200k-text-gemini25",
                    "provider_id": "google",
                    "model_id": "gemini-2.5-flash",
                    "display_name": "Gemini 2.5 Flash",
                    "service_tier": "standard",
                    "context_window": ">200k",
                    "modality": "text",
                    "category": "gemini_2_5",
                    "input_unit": None,
                    "input_price": None,
                    "output_unit": "per_1M_tokens",
                    "output_price": 2.5,
                    "cache_read_price": None,
                    "currency": "USD",
                    "is_active": True,
                },
                {
                    "pricing_id": "google-skip-me-image-generation",
                    "provider_id": "google",
                    "model_id": "imagen",
                    "display_name": "Imagen",
                    "service_tier": "standard",
                    "modality": "image",
                    "input_unit": "per_image",
                    "input_price": 0.04,
                    "output_unit": None,
                    "output_price": None,
                    "cache_read_price": None,
                    "currency": "USD",
                    "is_active": True,
                },
            ],
        }
        rows = normalize_snapshot(snap, "2026-08-10")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pricing_id"], "google-gemini-2.5-flash-standard-200k-text-gemini25")
        self.assertEqual(rows[0]["output_price"], 2.5)
        self.assertIsNone(rows[0]["input_price"])

    def test_dashboard_start_date_cutoff(self) -> None:
        day_before = (
            date.fromisoformat(DASHBOARD_START_DATE) - timedelta(days=1)
        ).isoformat()
        self.assertFalse(include_dashboard_date(day_before))
        self.assertTrue(include_dashboard_date(DASHBOARD_START_DATE))
        self.assertFalse(include_dashboard_date(""))
        self.assertFalse(include_dashboard_date(None or ""))


class BuildModelsTest(unittest.TestCase):
    def test_sets_deprecated_on_when_display_name_becomes_retired(self) -> None:
        series = [
            {
                "date": "2026-07-24",
                "pricing_id": "anthropic-claude-opus-4.1-standardapi",
                "provider_id": "anthropic",
                "model_id": "claude-opus-4.1",
                "display_name": "Claude Opus 4.1",
                "category": "standard_api",
                "input_price": 15.0,
                "output_price": 75.0,
                "cached_input_price": 1.5,
                "currency": "USD",
                "is_active": True,
            },
            {
                "date": "2026-07-30",
                "pricing_id": "anthropic-claude-opus-4.1-standardapi",
                "provider_id": "anthropic",
                "model_id": "claude-opus-4.1",
                "display_name": "Claude Opus 4.1 ( retired )",
                "category": "standard_api",
                "input_price": 15.0,
                "output_price": 75.0,
                "cached_input_price": 1.5,
                "currency": "USD",
                "is_active": True,
            },
        ]
        schema_by_date = {
            "2026-07-24": "2.1.0",
            "2026-07-30": "2.1.0",
        }

        models = build_models(series, schema_by_date)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["deprecated_on"], "2026-07-30")
        self.assertTrue(models[0]["currently_active"])


class BuildEquivalenceTest(unittest.TestCase):
    def test_builds_daily_aligned_budget(self) -> None:
        models = [
            {
                "provider_id": "anthropic",
                "model_id": "claude-opus-5",
                "display_name": "Claude Opus 5",
                "latest_input": 5.0,
                "latest_output": 25.0,
                "currency": "USD",
                "currently_active": True,
            },
            {
                "provider_id": "anthropic",
                "model_id": "claude-haiku-4.5",
                "display_name": "Claude Haiku 4.5",
                "latest_input": 1.0,
                "latest_output": 5.0,
                "currency": "USD",
                "currently_active": True,
            },
            {
                "provider_id": "deepseek",
                "model_id": "deepseek-v4-pro",
                "display_name": "DeepSeek V4 Pro",
                "latest_input": 0.435,
                "latest_output": 0.87,
                "currency": "USD",
                "currently_active": True,
            },
            {
                "provider_id": "deepseek",
                "model_id": "deepseek-v4-flash",
                "display_name": "DeepSeek V4 Flash",
                "latest_input": 0.14,
                "latest_output": 0.28,
                "currency": "USD",
                "currently_active": True,
            },
        ]
        index = {
            "generated_at": "2026-08-13T00:00:00Z",
            "first_date": "2026-06-17",
            "last_date": "2026-08-13",
            "snapshot_count": 17,
        }

        eq = build_equivalence(models, index, live_model_map={})
        self.assertEqual(eq["pricing_snapshot_date"], "2026-08-13")
        self.assertEqual(eq["tokenizer_ledger"]["cadence"], "daily")
        self.assertEqual(eq["wrapper_runs"]["cadence"], "historical")
        self.assertEqual(eq["wrapper_runs"]["status"], "retired")
        self.assertEqual(eq["tasks"][0]["task_id"], "A")
        self.assertIn("suiteLong", eq["task_packs"])
        self.assertEqual(len(eq["chat_transcript"]), 20)
        self.assertIn("provider_auth", eq)
        self.assertIn("anthropic", eq["provider_auth"]["requirements"])
        self.assertIn("GEMINI_API_KEY", eq["provider_auth"]["requirements"]["google"]["env"])
        # Amazon is a tracked provider on the equivalence surface.
        self.assertIn("aws", eq["provider_auth"]["requirements"])

        # Only flagship + workhorse; the middle "default" tier was dropped.
        self.assertEqual(eq["tiers"], ["flagship", "workhorse"])
        two_tiers = eq["selected_models_by_mode"]["two"]
        self.assertEqual(len(two_tiers), 4)
        self.assertTrue(all(row["tier"] in ("flagship", "workhorse") for row in two_tiers))
        self.assertNotIn("default", {row["tier"] for row in eq["selected_models"]})

        self.assertEqual(eq["runs_per_year"], 365)
        budget = eq["budget"]["two"]["suiteLong"]
        self.assertGreater(budget["annual_usd"], 0)
        # `/pricing` prices the alternative cadences from this map; it read as
        # em-dashes for as long as the builder omitted it.
        by_cadence = budget["annual_usd_by_cadence"]
        self.assertEqual(budget["annual_usd"], by_cadence["daily"])
        self.assertAlmostEqual(by_cadence["daily"], budget["per_run_usd"] * 365)
        self.assertGreater(by_cadence["daily"], by_cadence["weekly"])
        self.assertGreater(by_cadence["weekly"], by_cadence["monthly"])

        # Tasks expose the invariant denominator the index normalizes on.
        self.assertGreater(eq["tasks"][0]["input_chars"], 0)
        self.assertIn("prompt", eq["tasks"][0])
        note = eq["package_cost_note"]
        self.assertIn("daily generated meter on tasks A–F", note)
        self.assertIn("daily tokenizer ledger on tasks A/B/C/D/F", note)
        self.assertNotIn("daily task E wrapper counts", note)


class BuildTokenRunsTest(unittest.TestCase):
    def test_normalizes_density_and_flags_censored_output(self) -> None:
        rows = [
            {
                "run_date": POST_EPOCH,
                "provider_id": "google",
                "tier": "flagship",
                "task_id": "A",
                "model_id": "gemini-3.1-pro",
                "api_model": "gemini-pro-latest",
                "tokens_in": 200,
                "tokens_out": 400,
                "input_chars": 1000,
                "output_cap": 400,
                "run_status": "ok",
                "usd_value_same_day": 0.01,
            },
            # Not ok → excluded.
            {
                "run_date": POST_EPOCH,
                "provider_id": "anthropic",
                "tier": "flagship",
                "task_id": "A",
                "tokens_in": None,
                "tokens_out": None,
                "run_status": "error",
            },
            # Dry run → excluded.
            {
                "run_date": POST_EPOCH,
                "provider_id": "xai",
                "tier": "flagship",
                "task_id": "A",
                "tokens_in": None,
                "tokens_out": None,
                "run_status": "dry_run",
            },
        ]

        out = build_token_runs(rows)

        self.assertEqual(len(out), 1)
        row = out[0]
        self.assertEqual(row["provider_id"], "google")
        self.assertEqual(row["tokens_total"], 600)
        # 200 tokens over 1000 chars → 200 tokens per 1K chars.
        self.assertAlmostEqual(row["tokens_in_per_1k_chars"], 200.0)
        # Output reached the cap, so verbosity is unobserved.
        self.assertTrue(row["output_censored"])
        self.assertEqual(row["replicate_count"], 1)

    def test_medians_workhorse_replicates(self) -> None:
        rows = [
            {
                "run_date": POST_EPOCH,
                "provider_id": "deepseek",
                "tier": "workhorse",
                "task_id": "A",
                "replicate": r,
                "tokens_in": tokens_in,
                "tokens_out": 10,
                "input_chars": 1000,
                "output_cap": 400,
                "run_status": "ok",
                "usd_value_same_day": 0.01,
            }
            for r, tokens_in in ((1, 100), (2, 200), (3, 300))
        ]
        out = build_token_runs(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["tokens_in"], 200)  # median
        self.assertEqual(out[0]["replicate_count"], 3)

    def test_uses_corpus_chars_when_row_omits_them(self) -> None:
        rows = [
            {
                "run_date": POST_EPOCH,
                "provider_id": "deepseek",
                "tier": "workhorse",
                "task_id": "A",
                "tokens_in": 40,
                "tokens_out": 10,
                "output_cap": 400,
                "run_status": "ok",
            }
        ]
        out = build_token_runs(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["input_chars"], len(TASK_PROMPTS["A"]))
        self.assertFalse(out[0]["output_censored"])

    def test_task_e_density_medians_per_replicate_ratios(self) -> None:
        rows = [
            {
                "run_date": POST_EPOCH,
                "provider_id": "openai",
                "tier": "workhorse",
                "task_id": "E",
                "replicate": 1,
                "tokens_in": 90,
                "tokens_out": 10,
                "input_chars": 900,
                "run_status": "ok",
            },
            {
                "run_date": POST_EPOCH,
                "provider_id": "openai",
                "tier": "workhorse",
                "task_id": "E",
                "replicate": 2,
                "tokens_in": 180,
                "tokens_out": 10,
                "input_chars": 600,
                "run_status": "ok",
            },
            {
                "run_date": POST_EPOCH,
                "provider_id": "openai",
                "tier": "workhorse",
                "task_id": "E",
                "replicate": 3,
                "tokens_in": 330,
                "tokens_out": 10,
                "input_chars": 3300,
                "run_status": "ok",
            },
        ]
        out = build_token_runs(rows)
        self.assertEqual(len(out), 1)
        # ratios are 100, 300, 100 -> median = 100
        self.assertEqual(out[0]["tokens_in_per_1k_chars"], 100.0)


class RunDateTest(unittest.TestCase):
    def test_prefers_run_date_but_falls_back_to_the_old_week_anchor(self) -> None:
        self.assertEqual(run_date_of({"run_date": "2026-08-22"}), "2026-08-22")
        self.assertEqual(run_date_of({"run_week": "2026-08-17"}), "2026-08-17")
        self.assertEqual(run_date_of({}), "")

    def test_token_runs_drop_rows_from_before_the_epoch(self) -> None:
        """Pre-epoch rows were collected on the weekly cadence, so charting them
        beside daily rows would read as drift in the models rather than a change
        in how the meter was run."""
        stale = {
            "run_week": PRE_EPOCH,
            "provider_id": "google",
            "tier": "flagship",
            "task_id": "A",
            "tokens_in": 200,
            "tokens_out": 10,
            "input_chars": 1000,
            "output_cap": 400,
            "run_status": "ok",
        }
        current = {**stale, "run_week": None, "run_date": POST_EPOCH}

        out = build_token_runs([stale, current])

        self.assertEqual([row["date"] for row in out], [POST_EPOCH])


class DailyMeterAnchorTest(unittest.TestCase):
    def test_anchor_is_the_calendar_day_not_the_week_start(self) -> None:
        """A Monday-snapped anchor would make every run in a week collide on the
        replace key, so a daily cadence would silently keep one row per week."""
        wednesday = date(2026, 8, 26)
        self.assertEqual(current_run_date(wednesday), "2026-08-26")
        self.assertNotEqual(current_run_date(wednesday), "2026-08-24")
        self.assertEqual(
            len({current_run_date(date(2026, 8, 24) + timedelta(days=n)) for n in range(7)}),
            7,
        )


class LedgerFitTest(unittest.TestCase):
    """The overhead/content split, and the reason it exists."""

    # Real task lengths — the span between them is what makes the fit possible.
    # Real task lengths. "F" is the long task with natural text; "D" is the long
    # task that repeats one sentence 800 times and cannot set a slope, so tests
    # about the rate use F and tests about the fallback use D alone.
    CHARS = {"A": 157, "B": 843, "C": 217, "D": 25_743, "F": 9_626}

    def setUp(self) -> None:
        patcher = mock.patch.object(
            build_dashboard_data, "DEGENERATE_TASK_IDS", frozenset({"D"})
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self, date_: str, tokens: dict[str, int], provider: str = "xai") -> list[dict]:
        return [
            {
                "date": date_,
                "provider_id": provider,
                "tier": "flagship",
                "task_id": task_id,
                "model_id": "grok-4.6",
                "api_model": "grok-4.6",
                "input_chars": self.CHARS[task_id],
                "tokens_in": count,
                "run_status": "ok",
            }
            for task_id, count in tokens.items()
        ]

    def synth(self, fixed: int, rate: float, long_task: str = "F") -> dict[str, int]:
        tasks = ["A", "B", "C", long_task]
        return {task: round(fixed + rate * self.CHARS[task]) for task in tasks}

    def test_recovers_the_parameters_it_was_built_from(self) -> None:
        rows = self.rows("2026-08-23", self.synth(fixed=655, rate=0.1557))
        (fit,) = build_ledger_fits(rows)

        self.assertTrue(fit["fit_ok"])
        self.assertAlmostEqual(fit["fixed_overhead_tokens"], 655, delta=1.5)
        self.assertAlmostEqual(fit["content_density_per_1k_chars"], 155.7, delta=0.5)

    def test_a_pure_overhead_change_does_not_move_content_density(self) -> None:
        """The observation the measure was added for.

        On 2026-08-23 grok-4.6 gained exactly 430 prompt tokens on all four tasks
        at once — on a 157-character prompt and a 25,743-character one alike. A
        vocabulary change scales with content; this did not, so it was scaffolding.
        The per-task density read that as +2,739 tokens/1K chars on task A and +17
        on task D. Content density must read it as nothing at all.
        """
        before = {"A": 237, "B": 357, "C": 270, "F": 4233}
        after = {task: count + 430 for task, count in before.items()}

        fits = build_ledger_fits(self.rows("2026-08-22", before) + self.rows("2026-08-23", after))
        by_date = {fit["date"]: fit for fit in fits}

        # Guard against the assertion below passing because both sides are None.
        self.assertIsNotNone(by_date["2026-08-22"]["content_density_per_1k_chars"])
        self.assertEqual(
            by_date["2026-08-22"]["content_density_per_1k_chars"],
            by_date["2026-08-23"]["content_density_per_1k_chars"],
        )
        self.assertAlmostEqual(
            by_date["2026-08-23"]["fixed_overhead_tokens"]
            - by_date["2026-08-22"]["fixed_overhead_tokens"],
            430,
            delta=0.5,
        )

    def test_narrow_char_span_is_not_fitted(self) -> None:
        """2026-08-21 collected A, B and C but not D — a 5.4x span. Fitting it
        yields a rate 3% off the next day's four-task fit, which would render as a
        step change caused purely by the task mix."""
        short = {task: count for task, count in self.synth(655, 0.1557).items() if task != "F"}
        (fit,) = build_ledger_fits(self.rows("2026-08-21", short))

        self.assertLess(fit["char_span_ratio"], MIN_FIT_CHAR_SPAN)
        self.assertFalse(fit["fit_ok"])
        self.assertIsNone(fit["content_density_per_1k_chars"])
        self.assertIsNone(fit["fixed_overhead_tokens"])

    def test_too_few_tasks_is_not_fitted(self) -> None:
        two = {"A": 237, "D": 4233}
        (fit,) = build_ledger_fits(self.rows("2026-08-23", two))

        self.assertEqual(fit["task_count"], 2)
        self.assertLess(fit["task_count"], MIN_FIT_TASKS)
        self.assertFalse(fit["fit_ok"])

    def test_a_repetitive_long_task_withholds_the_rate_but_not_the_overhead(self) -> None:
        """Task D clears both arithmetic guards and still cannot set a slope.

        It is 96% of the suite's characters, so it is the only reason any day spans
        10x — but it is one sentence repeated 800 times, so its marginal cost is the
        cost of re-merging a known phrase. That is what made 13 of 14 model rows
        report 155.7 tokens/1K chars to within 0.2 and look like consensus between
        vocabularies that genuinely differ.

        The intercept is anchored by A, B and C down at 157-843 characters, so it
        survives a wrong slope and keeps being published.
        """
        (fit,) = build_ledger_fits(self.rows("2026-08-23", self.synth(655, 0.1557, long_task="D")))

        self.assertTrue(fit["fit_ok"])
        self.assertGreaterEqual(fit["char_span_ratio"], MIN_FIT_CHAR_SPAN)
        self.assertFalse(fit["density_ok"])
        self.assertIsNone(fit["content_density_per_1k_chars"])
        self.assertAlmostEqual(fit["fixed_overhead_tokens"], 655, delta=1.5)
        # And it fell back to the full task set rather than refusing to fit: the
        # pre-task-F record depends on that, since A/B/C alone span only 5.4x.
        self.assertEqual(fit["fit_task_ids"], ["A", "B", "C", "D"])

    def test_task_d_is_excluded_once_a_natural_long_task_is_present(self) -> None:
        """With F available the slope must not rest on D, even though D is longer.

        D would otherwise dominate by leverage — 25,743 characters against F's
        9,626 — and reimpose the filler's rate on the fit it was excluded from.
        """
        tokens = self.synth(655, 0.1557)
        tokens["D"] = 99_999  # A rate nothing else shares; it must not show up.
        (fit,) = build_ledger_fits(self.rows("2026-08-23", tokens))

        self.assertEqual(fit["fit_task_ids"], ["A", "B", "C", "F"])
        self.assertEqual(fit["task_ids"], ["A", "B", "C", "D", "F"])
        self.assertTrue(fit["density_ok"])
        self.assertAlmostEqual(fit["content_density_per_1k_chars"], 155.7, delta=0.5)
        self.assertAlmostEqual(fit["fixed_overhead_tokens"], 655, delta=1.5)

    def test_the_live_corpus_can_now_set_a_content_rate(self) -> None:
        """Reads the real prompts, not the fixture, so the corpus that actually
        ships is what gets checked. Every task now clears the variety floor —
        task D's repeated filler was replaced in corpus 2.0.0 — and the set has
        to clear both fit guards on its own."""
        self.assertEqual(sorted(DEGENERATE_TASK_IDS), [])

        natural = [t for t in METER_TASK_IDS if t not in DEGENERATE_TASK_IDS]
        for task in natural:
            self.assertGreater(lexical_variety(task), MIN_LEXICAL_VARIETY)

        chars = [len(TASK_PROMPTS[t]) for t in natural]
        self.assertGreaterEqual(len(natural), MIN_FIT_TASKS)
        self.assertGreaterEqual(max(chars) / min(chars), MIN_FIT_CHAR_SPAN)

    def test_failed_rows_are_excluded(self) -> None:
        rows = self.rows("2026-08-23", self.synth(655, 0.1557))
        for row in rows:
            row["run_status"] = "error"

        self.assertEqual(build_ledger_fits(rows), [])

    def test_one_fit_per_provider_tier_day(self) -> None:
        rows = self.rows("2026-08-23", self.synth(655, 0.1557), provider="xai")
        rows += self.rows("2026-08-23", self.synth(22, 0.1556), provider="openai")

        fits = {fit["provider_id"]: fit for fit in build_ledger_fits(rows)}

        self.assertEqual(sorted(fits), ["openai", "xai"])
        # Same tokenizer rate, wildly different scaffolding — the split the
        # per-task density charts could not show.
        self.assertAlmostEqual(
            fits["xai"]["content_density_per_1k_chars"],
            fits["openai"]["content_density_per_1k_chars"],
            delta=0.5,
        )
        self.assertGreater(
            fits["xai"]["fixed_overhead_tokens"],
            fits["openai"]["fixed_overhead_tokens"] + 500,
        )


class CostFixture(unittest.TestCase):
    """One complete instrumented day, built through the real event constructor.

    Every event is priced at 1.00/2.00 per 1M tokens on 100 input and 50 output
    tokens, so a meter request is worth 0.0002 and a ledger probe 0.0001. A
    complete panel row is therefore 8 meter requests (A/B/C/D/F plus E turns
    1-3) and 5 ledger requests: 0.0021 a day, which is small enough to keep the
    arithmetic in the assertions readable and exact enough to reconcile.
    """

    PANEL = [
        {
            "provider_id": "openai",
            "tier": "flagship",
            "model_id": "chat-latest",
            "display_name": "OpenAI Flagship",
        },
        {
            "provider_id": "deepseek",
            "tier": "workhorse",
            "model_id": "deepseek-v4-flash",
            "display_name": "DeepSeek Workhorse",
        },
    ]
    INPUT_PRICE = 1.0
    OUTPUT_PRICE = 2.0
    METER_TOKENS = (100, 50)
    LEDGER_TOKENS = (100, 0)
    # Per panel row, per day, at scale 1.
    ROW_DAY_USD = 8 * (100 / 1e6 * 1.0 + 50 / 1e6 * 2.0) + 5 * (100 / 1e6 * 1.0)

    @property
    def solo_panel(self) -> list[dict]:
        return self.PANEL[:1]

    def meter_event(
        self,
        date_: str,
        entry: dict,
        task_id: str,
        turn: int | None,
        *,
        scale: float = 1.0,
        canonical: bool = True,
        attempt: int = 1,
        run_suffix: str = "",
        prices: tuple[float | None, float | None] | None = None,
    ) -> dict:
        input_price, output_price = prices or (self.INPUT_PRICE, self.OUTPUT_PRICE)
        return build_cost_event(
            date=date_,
            run_at=f"{date_}T00:00:00Z",
            source="meter",
            provider_id=entry["provider_id"],
            tier=entry["tier"],
            task_id=task_id,
            turn=turn,
            request_kind="generation",
            api_model=entry["model_id"],
            input_tokens=int(self.METER_TOKENS[0] * scale),
            output_tokens=int(self.METER_TOKENS[1] * scale),
            input_price_per_1m=input_price,
            output_price_per_1m=output_price,
            pricing_snapshot_date=date_,
            corpus_version="3.0.0",
            chat_corpus_version="2.0.0" if task_id == "E" else None,
            run_id=f"{date_}:two:1{run_suffix}",
            replicate=1,
            attempt=attempt,
            canonical=canonical,
        )

    def ledger_event(
        self,
        date_: str,
        entry: dict,
        task_id: str,
        *,
        scale: float = 1.0,
    ) -> dict:
        return build_cost_event(
            date=date_,
            run_at=f"{date_}T00:00:00Z",
            source="ledger",
            provider_id=entry["provider_id"],
            tier=entry["tier"],
            task_id=task_id,
            turn=None,
            request_kind="completion_probe",
            api_model=entry["model_id"],
            input_tokens=int(self.LEDGER_TOKENS[0] * scale),
            output_tokens=int(self.LEDGER_TOKENS[1] * scale),
            input_price_per_1m=self.INPUT_PRICE,
            output_price_per_1m=self.OUTPUT_PRICE,
            pricing_snapshot_date=date_,
            corpus_version="3.0.0",
            chat_corpus_version=None,
            run_id=f"{date_}:ledger",
            replicate=1,
            attempt=1,
            canonical=True,
        )

    def complete_events(
        self, date_: str, panel: list[dict] | None = None, scale: float = 1.0
    ) -> list[dict]:
        rows: list[dict] = []
        for entry in panel or self.PANEL:
            for task_id in GENERATING_TASK_IDS:
                turns = (
                    list(range(1, len(E_USER_PROMPTS) + 1)) if task_id == "E" else [None]
                )
                for turn in turns:
                    rows.append(self.meter_event(date_, entry, task_id, turn, scale=scale))
            for task_id in LEDGER_TASK_IDS:
                rows.append(self.ledger_event(date_, entry, task_id, scale=scale))
        return rows

    def events_for_range(
        self,
        start: str,
        end: str,
        panel: list[dict] | None = None,
        scale: float = 1.0,
    ) -> list[dict]:
        rows: list[dict] = []
        day = date.fromisoformat(start)
        last = date.fromisoformat(end)
        while day <= last:
            rows.extend(self.complete_events(day.isoformat(), panel=panel, scale=scale))
            day += timedelta(days=1)
        return rows


class BuildCostsTest(CostFixture):
    def test_complete_day_reconciles_cost_tree(self) -> None:
        costs = build_costs(self.complete_events(POST_EPOCH), self.PANEL)
        day = costs["daily"][0]

        self.assertTrue(day["complete"])
        leaves = [
            detail["estimated_cost_usd"]
            for row in day["provider_tiers"]
            for task in row["tasks"]
            for detail in task["details"]
        ]
        self.assertEqual(len(leaves), 13 * len(self.PANEL))
        self.assertAlmostEqual(sum(leaves), day["estimated_spend_usd"])
        self.assertAlmostEqual(day["estimated_spend_usd"], self.ROW_DAY_USD * len(self.PANEL))

        # Generation input/output is the meter; ledger probes are supporting spend.
        self.assertAlmostEqual(
            day["input_cost_usd"] + day["output_cost_usd"] + day["supporting_cost_usd"],
            day["estimated_spend_usd"],
        )
        self.assertAlmostEqual(day["supporting_cost_usd"], 5 * 0.0001 * len(self.PANEL))

        # Every level of the tree carries the same split, and the parents are the
        # sum of their children rather than an independently computed figure.
        for row in day["provider_tiers"]:
            self.assertAlmostEqual(
                sum(task["estimated_spend_usd"] for task in row["tasks"]),
                row["estimated_spend_usd"],
            )
            for task in row["tasks"]:
                self.assertAlmostEqual(
                    sum(detail["estimated_cost_usd"] for detail in task["details"]),
                    task["estimated_spend_usd"],
                )

        e_row = next(
            task for task in day["provider_tiers"][0]["tasks"] if task["task_id"] == "E"
        )
        self.assertEqual([d["turn"] for d in e_row["details"]], [1, 2, 3])

    def test_one_missing_e_turn_withholds_the_day(self) -> None:
        events = [
            e
            for e in self.complete_events(POST_EPOCH)
            if not (e["task_id"] == "E" and e["turn"] == 2)
        ]
        costs = build_costs(events, self.PANEL)

        day = costs["daily"][0]
        self.assertFalse(day["complete"])
        self.assertIn(
            {"source": "meter", "task_id": "E", "turn": 2},
            [
                {k: miss[k] for k in ("source", "task_id", "turn")}
                for miss in day["provider_tiers"][0]["missing_requests"]
            ],
        )
        self.assertIsNone(costs["latest_complete_date"])
        self.assertEqual(costs["latest_attempted_date"], POST_EPOCH)

    def test_a_missing_ledger_task_withholds_the_day(self) -> None:
        events = [
            e
            for e in self.complete_events(POST_EPOCH)
            if not (e["source"] == "ledger" and e["task_id"] == "D")
        ]
        self.assertFalse(build_costs(events, self.PANEL)["daily"][0]["complete"])

    def test_a_panel_row_with_no_events_withholds_the_day(self) -> None:
        events = self.complete_events(POST_EPOCH, panel=self.solo_panel)
        costs = build_costs(events, self.PANEL)

        day = costs["daily"][0]
        self.assertFalse(day["complete"])
        # The absent row is still named, so the reason is legible.
        self.assertEqual(
            [(row["provider_id"], row["tier"]) for row in day["provider_tiers"]],
            [(row["provider_id"], row["tier"]) for row in self.PANEL],
        )
        self.assertFalse(day["provider_tiers"][1]["complete"])
        self.assertEqual(day["provider_tiers"][1]["estimated_spend_usd"], 0.0)

    def test_an_incomplete_event_withholds_the_day_but_not_the_spend(self) -> None:
        """A priceless request means the day's real spend is unknown.

        The requests that did complete are still real money, so they stay in the
        total; what is withheld is the claim that the total is the whole day.
        """
        events = [
            e
            for e in self.complete_events(POST_EPOCH)
            if not (e["source"] == "meter" and e["task_id"] == "A")
        ]
        events.append(
            self.meter_event(
                POST_EPOCH, self.PANEL[0], "A", None, prices=(None, None)
            )
        )
        events.append(self.meter_event(POST_EPOCH, self.PANEL[1], "A", None))

        day = build_costs(events, self.PANEL)["daily"][0]

        self.assertFalse(day["complete"])
        self.assertEqual(day["incomplete_event_count"], 1)
        self.assertAlmostEqual(
            day["estimated_spend_usd"], self.ROW_DAY_USD * len(self.PANEL) - 0.0002
        )

    def test_an_abandoned_conversation_attempt_is_spent_but_not_required(self) -> None:
        """Task E can be retried; only the winning attempt is canonical.

        The abandoned turns were still billed, so they belong in the day's
        estimated spend — but completeness is judged on the canonical 1/2/3 set,
        or a retried conversation could never close a day.
        """
        events = self.complete_events(POST_EPOCH)
        events += [
            self.meter_event(
                POST_EPOCH,
                self.PANEL[0],
                "E",
                turn,
                canonical=False,
                attempt=1,
                run_suffix=":a1",
            )
            for turn in (1, 2)
        ]

        costs = build_costs(events, self.PANEL)
        day = costs["daily"][0]

        self.assertTrue(day["complete"])
        self.assertAlmostEqual(
            day["estimated_spend_usd"], self.ROW_DAY_USD * len(self.PANEL) + 2 * 0.0002
        )
        e_row = next(
            task for task in day["provider_tiers"][0]["tasks"] if task["task_id"] == "E"
        )
        self.assertEqual(len(e_row["details"]), 5)
        self.assertAlmostEqual(
            sum(detail["estimated_cost_usd"] for detail in e_row["details"]),
            e_row["estimated_spend_usd"],
        )

    def test_a_request_the_schedule_does_not_recognize_withholds_the_day(self) -> None:
        """A stale task id means the day is not the day the schedule describes.

        The corpus defines the expected request set, so an event outside it is a
        replay from another corpus rather than extra coverage, and a total built
        over it is not comparable with the days around it.
        """
        events = self.complete_events(POST_EPOCH)
        events.append(self.meter_event(POST_EPOCH, self.PANEL[0], "Z", None))

        day = build_costs(events, self.PANEL)["daily"][0]

        self.assertFalse(day["complete"])
        self.assertEqual(
            day["provider_tiers"][0]["unexpected_requests"],
            [{"source": "meter", "task_id": "Z", "turn": None}],
        )

    def test_pre_epoch_events_are_not_published(self) -> None:
        costs = build_costs(self.complete_events(PRE_EPOCH), self.PANEL)

        self.assertEqual(costs["daily"], [])
        self.assertIsNone(costs["latest_attempted_date"])
        self.assertEqual(costs["complete_start_date"], DASHBOARD_START_DATE)

    def test_no_events_is_pending_not_zero(self) -> None:
        costs = build_costs([], self.PANEL)

        self.assertEqual(costs["status"], "pending_first_complete_day")
        self.assertEqual(costs["daily"], [])
        self.assertIsNone(costs["latest_complete_date"])
        self.assertEqual(costs["panel_row_count"], len(self.PANEL))
        for period in ("current_run", "current_month", "year_to_date"):
            with self.subTest(period=period):
                comparison = costs["comparisons"][period]
                self.assertEqual(comparison["status"], "comparison_unavailable")
                self.assertIsNone(comparison["amount_usd"])
        self.assertIn("not an invoice", costs["note"])

    def test_expected_requests_are_derived_from_the_corpus(self) -> None:
        costs = build_costs([], self.PANEL)

        # Corpus order, with the conversation expanded in place into its turns.
        expected = []
        for task_id in GENERATING_TASK_IDS:
            if task_id == "E":
                expected += [
                    {"task_id": "E", "turn": turn}
                    for turn in range(1, len(E_USER_PROMPTS) + 1)
                ]
            else:
                expected.append({"task_id": task_id, "turn": None})
        self.assertEqual(costs["expected_meter_requests_per_row"], expected)
        self.assertEqual(
            costs["expected_ledger_requests_per_row"],
            [{"task_id": task_id, "turn": None} for task_id in LEDGER_TASK_IDS],
        )

    def test_latest_attempted_date_can_be_widened_by_the_caller(self) -> None:
        later = (date.fromisoformat(POST_EPOCH) + timedelta(days=4)).isoformat()
        costs = build_costs(self.complete_events(POST_EPOCH), self.PANEL, later)

        self.assertEqual(costs["latest_attempted_date"], later)
        self.assertEqual(costs["latest_complete_date"], POST_EPOCH)


class CostComparisonTest(CostFixture):
    def day(self, offset: int) -> str:
        return (date.fromisoformat(POST_EPOCH) + timedelta(days=offset)).isoformat()

    def test_current_run_compares_the_two_most_recent_complete_days(self) -> None:
        events = self.complete_events(self.day(0), panel=self.solo_panel, scale=1)
        events += self.complete_events(self.day(1), panel=self.solo_panel, scale=2)
        run = build_costs(events, self.solo_panel)["comparisons"]["current_run"]

        self.assertEqual(run["status"], "ok")
        self.assertAlmostEqual(run["amount_usd"], self.ROW_DAY_USD * 2)
        self.assertAlmostEqual(run["previous_amount_usd"], self.ROW_DAY_USD)
        self.assertAlmostEqual(run["delta_usd"], self.ROW_DAY_USD)
        self.assertAlmostEqual(run["delta_pct"], 100.0)
        self.assertEqual(run["previous_window"]["end_date"], self.day(0))

    def test_the_first_complete_day_is_a_new_baseline(self) -> None:
        run = build_costs(
            self.complete_events(self.day(0), panel=self.solo_panel), self.solo_panel
        )["comparisons"]["current_run"]

        self.assertEqual(run["status"], "new_baseline")
        self.assertAlmostEqual(run["amount_usd"], self.ROW_DAY_USD)
        self.assertIsNone(run["previous_amount_usd"])
        self.assertIsNone(run["delta_usd"])
        self.assertIsNone(run["delta_pct"])

    def test_a_zero_baseline_yields_no_percentage(self) -> None:
        events = self.complete_events(self.day(0), panel=self.solo_panel, scale=0)
        events += self.complete_events(self.day(1), panel=self.solo_panel, scale=1)
        run = build_costs(events, self.solo_panel)["comparisons"]["current_run"]

        self.assertEqual(run["status"], "new_baseline")
        self.assertEqual(run["previous_amount_usd"], 0.0)
        self.assertAlmostEqual(run["delta_usd"], self.ROW_DAY_USD)
        self.assertIsNone(run["delta_pct"])

    def test_the_prior_month_window_clamps_to_the_shorter_month(self) -> None:
        """31 March has no 31 February; the window ends on the 28th instead."""
        events = self.events_for_range(
            "2027-02-01", "2027-02-28", panel=self.solo_panel, scale=1
        )
        events += self.events_for_range(
            "2027-03-01", "2027-03-31", panel=self.solo_panel, scale=2
        )

        month = build_costs(events, self.solo_panel)["comparisons"]["current_month"]

        self.assertEqual(month["status"], "ok")
        self.assertEqual(month["current_window"], {"start_date": "2027-03-01", "end_date": "2027-03-31"})
        self.assertEqual(
            month["previous_window"], {"start_date": "2027-02-01", "end_date": "2027-02-28"}
        )
        self.assertAlmostEqual(month["amount_usd"], 31 * self.ROW_DAY_USD * 2)
        self.assertAlmostEqual(month["previous_amount_usd"], 28 * self.ROW_DAY_USD)

    def test_the_prior_month_of_january_is_the_previous_december(self) -> None:
        events = self.events_for_range(
            "2028-12-01", "2028-12-15", panel=self.solo_panel, scale=1
        )
        events += self.events_for_range(
            "2029-01-01", "2029-01-15", panel=self.solo_panel, scale=1
        )

        month = build_costs(events, self.solo_panel)["comparisons"]["current_month"]

        self.assertEqual(month["status"], "ok")
        self.assertEqual(
            month["previous_window"], {"start_date": "2028-12-01", "end_date": "2028-12-15"}
        )
        self.assertAlmostEqual(month["previous_amount_usd"], 15 * self.ROW_DAY_USD)
        self.assertAlmostEqual(month["delta_usd"], 0.0)

    def test_year_to_date_clamps_a_leap_day_against_a_common_year(self) -> None:
        events = self.events_for_range(
            "2027-01-01", "2027-02-28", panel=self.solo_panel, scale=1
        )
        events += self.events_for_range(
            "2028-01-01", "2028-02-29", panel=self.solo_panel, scale=1
        )

        ytd = build_costs(events, self.solo_panel)["comparisons"]["year_to_date"]

        self.assertEqual(ytd["status"], "ok")
        self.assertEqual(
            ytd["current_window"], {"start_date": "2028-01-01", "end_date": "2028-02-29"}
        )
        self.assertEqual(
            ytd["previous_window"], {"start_date": "2027-01-01", "end_date": "2027-02-28"}
        )
        self.assertAlmostEqual(ytd["amount_usd"], 60 * self.ROW_DAY_USD)
        self.assertAlmostEqual(ytd["previous_amount_usd"], 59 * self.ROW_DAY_USD)

    def test_a_prior_period_entirely_before_the_epoch_is_a_new_baseline(self) -> None:
        events = self.events_for_range(self.day(0), self.day(1), panel=self.solo_panel)
        comparisons = build_costs(events, self.solo_panel)["comparisons"]

        for period in ("current_month", "year_to_date"):
            with self.subTest(period=period):
                comparison = comparisons[period]
                self.assertEqual(comparison["status"], "new_baseline")
                self.assertAlmostEqual(comparison["amount_usd"], 2 * self.ROW_DAY_USD)
                self.assertIsNone(comparison["previous_amount_usd"])
                self.assertIsNone(comparison["delta_pct"])
        # The epoch, not the 1st, is the floor for the month the epoch lands in.
        self.assertEqual(
            comparisons["current_month"]["current_window"]["start_date"],
            DASHBOARD_START_DATE,
        )

    def test_a_missing_scheduled_day_withholds_the_calendar_comparisons(self) -> None:
        events = self.complete_events(self.day(0), panel=self.solo_panel)
        events += self.complete_events(self.day(2), panel=self.solo_panel)
        comparisons = build_costs(events, self.solo_panel)["comparisons"]

        for period in ("current_month", "year_to_date"):
            with self.subTest(period=period):
                comparison = comparisons[period]
                self.assertEqual(comparison["status"], "comparison_unavailable")
                self.assertIsNone(comparison["amount_usd"])
        # A run-over-run comparison is still honest: both runs are complete, and
        # the window it names says which run it used.
        self.assertEqual(comparisons["current_run"]["status"], "ok")
        self.assertEqual(comparisons["current_run"]["previous_window"]["end_date"], self.day(0))

    def test_an_incomplete_current_window_publishes_no_amount(self) -> None:
        comparison = build_cost_comparison(
            {
                "start_date": "2026-09-03",
                "end_date": "2026-09-05",
                "amount_usd": 1.5,
                "complete": False,
                "scheduled_date_count": 3,
            },
            {
                "start_date": "2026-08-03",
                "end_date": "2026-08-05",
                "amount_usd": 1.0,
                "complete": True,
                "scheduled_date_count": 3,
            },
        )

        self.assertEqual(comparison["status"], "comparison_unavailable")
        self.assertIsNone(comparison["amount_usd"])
        self.assertIsNone(comparison["previous_amount_usd"])
        self.assertIsNone(comparison["delta_usd"])

    def test_an_incomplete_prior_window_still_publishes_the_amount(self) -> None:
        comparison = build_cost_comparison(
            {
                "start_date": "2026-09-03",
                "end_date": "2026-09-05",
                "amount_usd": 1.5,
                "complete": True,
                "scheduled_date_count": 3,
            },
            {
                "start_date": "2026-08-03",
                "end_date": "2026-08-05",
                "amount_usd": 1.0,
                "complete": False,
                "scheduled_date_count": 3,
            },
        )

        self.assertEqual(comparison["status"], "comparison_unavailable")
        self.assertAlmostEqual(comparison["amount_usd"], 1.5)
        self.assertIsNone(comparison["previous_amount_usd"])
        self.assertIsNone(comparison["delta_pct"])


class CostEpochPublicationTest(CostFixture):
    def test_the_epoch_is_the_shared_september_restart(self) -> None:
        self.assertEqual(DASHBOARD_START_DATE, "2026-09-03")

    def equivalence(self, **kwargs) -> dict:
        models = [
            {
                "provider_id": "anthropic",
                "model_id": "claude-opus-5",
                "display_name": "Claude Opus 5",
                "latest_input": 5.0,
                "latest_output": 25.0,
                "currency": "USD",
                "currently_active": True,
            },
            {
                "provider_id": "anthropic",
                "model_id": "claude-haiku-4.5",
                "display_name": "Claude Haiku 4.5",
                "latest_input": 1.0,
                "latest_output": 5.0,
                "currency": "USD",
                "currently_active": True,
            },
        ]
        index = {
            "generated_at": "2026-09-03T00:00:00Z",
            "first_date": DASHBOARD_START_DATE,
            "last_date": DASHBOARD_START_DATE,
            "snapshot_count": 1,
        }
        return build_equivalence(models, index, live_model_map={}, **kwargs)

    def test_publishes_the_canonical_task_sets_and_current_e_metadata(self) -> None:
        eq = self.equivalence(cost_events=[])

        self.assertEqual(eq["task_ids"], list(TASK_IDS))
        self.assertEqual(eq["generating_task_ids"], list(GENERATING_TASK_IDS))
        self.assertEqual(eq["ledger_task_ids"], list(LEDGER_TASK_IDS))
        self.assertEqual(eq["dashboard_start_date"], "2026-09-03")
        conversation = eq["conversation_task"]
        self.assertEqual(conversation["task_id"], "E")
        self.assertEqual(conversation["turns"], len(E_USER_PROMPTS))
        self.assertEqual(conversation["prompts"], list(E_USER_PROMPTS))

    def test_publishes_a_costs_block_scoped_to_the_selected_panel(self) -> None:
        eq = self.equivalence(cost_events=[])

        costs = eq["costs"]
        self.assertEqual(costs["complete_start_date"], DASHBOARD_START_DATE)
        self.assertEqual(costs["status"], "pending_first_complete_day")
        # Derived from the panel the meter actually runs, never a written-down count.
        self.assertEqual(costs["panel_row_count"], len(eq["selected_models_by_mode"]["two"]))

    def test_costs_roll_up_the_events_the_builder_is_given(self) -> None:
        panel = [
            {"provider_id": "anthropic", "tier": "flagship", "model_id": "claude-opus-5"},
            {"provider_id": "anthropic", "tier": "workhorse", "model_id": "claude-haiku-4.5"},
        ]
        eq = self.equivalence(cost_events=self.complete_events(POST_EPOCH, panel=panel))

        costs = eq["costs"]
        self.assertEqual(costs["latest_complete_date"], POST_EPOCH)
        self.assertEqual(costs["status"], "active")
        self.assertAlmostEqual(
            costs["daily"][0]["estimated_spend_usd"], self.ROW_DAY_USD * 2
        )

    def test_the_wrapper_archive_ignores_the_epoch(self) -> None:
        """The epoch keeps the *live* series comparable; the archive is neither.

        Wrapper collection is retired and every wrapper row predates the epoch,
        so scoping the archive to it would publish an empty history and read as
        data loss. It is published whole, flagged as unscoped, and nothing on
        the current surface may read it as task E.
        """
        stale = {
            "run_date": PRE_EPOCH,
            "provider_id": "openai",
            "tier": "flagship",
            "task_id": "E",
            "tokens_in": 4000,
            "run_status": "ok",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wrapper_runs.json"
            path.write_text(json.dumps({"rows": [stale]}))
            with mock.patch.object(build_dashboard_data, "WRAPPER_RUNS_FILE", path):
                eq = self.equivalence(cost_events=[])

        wrapper = eq["wrapper_runs"]
        self.assertEqual(wrapper["status"], "retired")
        self.assertFalse(wrapper["epoch_scoped"])
        self.assertEqual(wrapper["row_count"], 1)
        self.assertEqual(wrapper["ok_row_count"], 1)
        self.assertEqual(wrapper["last_date"], PRE_EPOCH)

    def test_ui_facing_ledger_note_drops_content_density_but_keeps_the_fits(self) -> None:
        eq = self.equivalence(cost_events=[])

        ledger = eq["tokenizer_ledger"]
        self.assertNotIn("density", ledger["note"].lower())
        self.assertIn("fits", ledger)


if __name__ == "__main__":
    unittest.main()
