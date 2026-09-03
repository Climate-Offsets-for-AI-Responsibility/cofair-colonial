#!/usr/bin/env python3
"""Unit tests for dashboard artifact normalization."""
from __future__ import annotations

import sys
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
    build_equivalence,
    build_ledger_fits,
    build_models,
    build_token_runs,
    include_dashboard_date,
    normalize_snapshot,
    run_date_of,
)
from run_equivalence_tasks import current_run_date
from task_corpus import (
    DEGENERATE_TASK_IDS,
    METER_TASK_IDS,
    MIN_LEXICAL_VARIETY,
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
        self.assertEqual(eq["wrapper_runs"]["cadence"], "daily")
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


if __name__ == "__main__":
    unittest.main()
