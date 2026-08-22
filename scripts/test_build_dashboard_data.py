#!/usr/bin/env python3
"""Unit tests for dashboard artifact normalization."""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_dashboard_data import (
    DASHBOARD_START_DATE,
    build_equivalence,
    build_models,
    build_token_runs,
    include_dashboard_date,
    normalize_snapshot,
    run_date_of,
)
from run_equivalence_tasks import current_run_date
from task_corpus import TASK_PROMPTS


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
                "run_date": "2026-08-22",
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
                "run_date": "2026-08-22",
                "provider_id": "anthropic",
                "tier": "flagship",
                "task_id": "A",
                "tokens_in": None,
                "tokens_out": None,
                "run_status": "error",
            },
            # Dry run → excluded.
            {
                "run_date": "2026-08-22",
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
                "run_date": "2026-08-22",
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
                "run_date": "2026-08-22",
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
            "run_week": "2026-08-10",
            "provider_id": "google",
            "tier": "flagship",
            "task_id": "A",
            "tokens_in": 200,
            "tokens_out": 10,
            "input_chars": 1000,
            "output_cap": 400,
            "run_status": "ok",
        }
        current = {**stale, "run_week": None, "run_date": "2026-08-22"}

        out = build_token_runs([stale, current])

        self.assertEqual([row["date"] for row in out], ["2026-08-22"])


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


if __name__ == "__main__":
    unittest.main()
