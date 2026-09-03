#!/usr/bin/env python3
"""Tests for the tokenizer-ledger runner's write behaviour.

Both cases here are about the ledger file being the only record of a day's
counts. Rows are keyed by (date, provider, tier, task), and a run *replaces*
matching keys, so anything that writes rows it did not really collect destroys
the real ones.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "ops"))
import run_tokenizer_ledger
import runbooks
import verify_token_runs as verify_ops
from cost_events import COST_EVENTS_FILE
from task_corpus import LEDGER_TASK_IDS, METER_TASK_IDS

PANEL = [
    {"provider_id": "google", "tier": "flagship", "model_id": "gemini-3.1-pro"},
    {"provider_id": "google", "tier": "workhorse", "model_id": "gemini-2.0-flash"},
]

COLLECTED = [
    {
        "date": "2026-09-02",
        "provider_id": "google",
        "tier": "flagship",
        "task_id": "A",
        "tokens_in": 30,
        "run_status": "ok",
    }
]


class DryRunTest(unittest.TestCase):
    """`--dry-run` must not write.

    It calls no provider, so every row it builds is `run_status: dry_run`. Writing
    them replaced the day's real counts with placeholders — and the command that
    did it was a check of which tasks a flag selects, which is precisely what a
    dry run is supposed to be safe for.
    """

    def _run(self, argv: list[str]) -> list[dict] | None:
        written: list[list[dict]] = []
        saved_cost_rows: list[list[dict]] = []
        before_exists = COST_EVENTS_FILE.exists()
        before_mtime = COST_EVENTS_FILE.stat().st_mtime if before_exists else None
        with (
            mock.patch.object(sys, "argv", ["run_tokenizer_ledger.py", *argv]),
            mock.patch.object(
                run_tokenizer_ledger,
                "load_equivalence",
                return_value={"selected_models_by_mode": {"two": PANEL}, "pricing_snapshot_date": "2026-09-01"},
            ),
            mock.patch.object(run_tokenizer_ledger, "load_panel", return_value=PANEL),
            mock.patch.object(
                run_tokenizer_ledger, "load_ledger", return_value=list(COLLECTED)
            ),
            mock.patch.object(
                run_tokenizer_ledger, "save_ledger", side_effect=written.append
            ),
            mock.patch.object(run_tokenizer_ledger, "load_cost_events", return_value=[]),
            mock.patch.object(
                run_tokenizer_ledger, "save_cost_events", side_effect=saved_cost_rows.append
            ),
            mock.patch.object(run_tokenizer_ledger, "env_for_provider", return_value=None),
        ):
            self.assertEqual(run_tokenizer_ledger.main(), 0)
        after_exists = COST_EVENTS_FILE.exists()
        after_mtime = COST_EVENTS_FILE.stat().st_mtime if after_exists else None
        self.assertEqual(after_exists, before_exists)
        self.assertEqual(after_mtime, before_mtime)
        return written[0] if written else None

    def test_dry_run_does_not_write_the_ledger(self) -> None:
        self.assertIsNone(self._run(["--tasks", "all", "--dry-run", "--date", "2026-09-02"]))

    def test_a_real_run_still_writes(self) -> None:
        """The guard must be about `--dry-run`, not about writing in general."""
        written = self._run(["--tasks", "all", "--date", "2026-09-02"])

        self.assertIsNotNone(written)
        self.assertTrue(any(row.get("run_status") == "missing_key" for row in written))

    def test_dry_run_reports_the_tasks_it_would_collect(self) -> None:
        """The information the dry run exists to give is still given."""
        with mock.patch("builtins.print") as printed:
            self._run(["--tasks", "all", "--dry-run", "--date", "2026-09-02"])

        payload = json.loads(printed.call_args[0][0])
        self.assertEqual(payload["event"], "tokenizer_ledger_dry_run")
        self.assertEqual(payload["tasks"], list(LEDGER_TASK_IDS))
        self.assertIsNone(payload["output_file"])

    def test_dry_run_does_not_write_cost_events(self) -> None:
        with (
            mock.patch.object(sys, "argv", ["run_tokenizer_ledger.py", "--tasks", "D", "--dry-run"]),
            mock.patch.object(
                run_tokenizer_ledger,
                "load_equivalence",
                return_value={"selected_models_by_mode": {"two": PANEL}, "pricing_snapshot_date": "2026-09-01"},
            ),
            mock.patch.object(run_tokenizer_ledger, "load_panel", return_value=PANEL),
            mock.patch.object(run_tokenizer_ledger, "load_ledger", return_value=list(COLLECTED)),
            mock.patch.object(run_tokenizer_ledger, "save_ledger"),
            mock.patch.object(run_tokenizer_ledger, "load_cost_events", return_value=[]),
            mock.patch.object(run_tokenizer_ledger, "save_cost_events") as save_cost_events,
            mock.patch.object(run_tokenizer_ledger, "env_for_provider", return_value=None),
        ):
            self.assertEqual(run_tokenizer_ledger.main(), 0)
        save_cost_events.assert_not_called()


class PricesForApiModelTest(unittest.TestCase):
    def test_resolves_prices_for_the_served_fallback_model(self) -> None:
        model = {
            "provider_id": "aws",
            "tier": "flagship",
            "model_id": "nova-premier",
            "input_price": 2.5,
            "output_price": 12.5,
            "api_candidates": [
                {
                    "model_id": "nova-pro",
                    "input_price": 0.8,
                    "output_price": 3.2,
                }
            ],
        }
        self.assertEqual(
            run_tokenizer_ledger.prices_for_api_model(model, "amazon.nova-pro-v1:0"),
            (0.8, 3.2),
        )

    def test_google_live_pro_id_uses_panel_prices(self) -> None:
        model = {
            "provider_id": "google",
            "tier": "flagship",
            "model_id": "gemini-3.1-pro",
            "input_price": 1.25,
            "output_price": 7.5,
            "api_candidates": [],
        }
        self.assertEqual(
            run_tokenizer_ledger.prices_for_api_model(model, "gemini-pro-latest"),
            (1.25, 7.5),
        )

    def test_google_live_flash_variant_uses_panel_prices(self) -> None:
        model = {
            "provider_id": "google",
            "tier": "workhorse",
            "model_id": "gemini-3-flash",
            "input_price": 0.3,
            "output_price": 0.9,
            "api_candidates": [],
        }
        self.assertEqual(
            run_tokenizer_ledger.prices_for_api_model(model, "gemini-flash-latest-high-res-exp"),
            (0.3, 0.9),
        )


class LedgerCostEventsTest(unittest.TestCase):
    MODEL = {
        "provider_id": "aws",
        "tier": "flagship",
        "model_id": "nova-premier",
        "input_price": 2.5,
        "output_price": 12.5,
        "api_candidates": [
            {"model_id": "nova-pro", "input_price": 0.8, "output_price": 3.2},
        ],
    }

    def _run(
        self,
        usage: SimpleNamespace,
        *,
        existing_cost_events: list[dict] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        ledger_rows: list[list[dict]] = []
        cost_rows: list[list[dict]] = []
        with (
            mock.patch.object(
                sys,
                "argv",
                ["run_tokenizer_ledger.py", "--tasks", "D", "--date", "2026-09-03"],
            ),
            mock.patch.object(
                run_tokenizer_ledger,
                "load_equivalence",
                return_value={
                    "selected_models_by_mode": {"two": [self.MODEL]},
                    "pricing_snapshot_date": "2026-09-01",
                },
            ),
            mock.patch.object(run_tokenizer_ledger, "load_panel", return_value=[self.MODEL]),
            mock.patch.object(run_tokenizer_ledger, "load_ledger", return_value=[]),
            mock.patch.object(run_tokenizer_ledger, "save_ledger", side_effect=ledger_rows.append),
            mock.patch.object(run_tokenizer_ledger, "load_cost_events", return_value=existing_cost_events or []),
            mock.patch.object(run_tokenizer_ledger, "save_cost_events", side_effect=cost_rows.append),
            mock.patch.object(run_tokenizer_ledger, "env_for_provider", return_value="key"),
            mock.patch.object(
                run_tokenizer_ledger,
                "count_prompt_tokens_text",
                return_value=("ok", usage, None, "amazon.nova-pro-v1:0"),
            ),
            mock.patch.object(run_tokenizer_ledger, "now_iso_z", return_value="2026-09-03T12:00:00Z"),
        ):
            self.assertEqual(run_tokenizer_ledger.main(), 0)
        return ledger_rows[0], cost_rows[0]

    def test_native_count_event_is_non_billable_zero_cost(self) -> None:
        usage = SimpleNamespace(
            tokens_in=120,
            tokens_out=0,
            request_kind="count_endpoint",
            billable=False,
        )
        _, saved_cost = self._run(usage)
        self.assertEqual(len(saved_cost), 1)
        event = saved_cost[0]
        self.assertEqual(event["source"], "ledger")
        self.assertEqual(event["request_kind"], "count_endpoint")
        self.assertEqual(event["input_tokens"], 120)
        self.assertEqual(event["output_tokens"], 0)
        self.assertEqual(event["estimated_cost_usd"], 0.0)
        self.assertEqual(event["replicate"], 1)
        self.assertEqual(event["attempt"], 1)
        self.assertTrue(event["canonical"])

    def test_completion_probe_event_uses_served_model_prices(self) -> None:
        usage = SimpleNamespace(
            tokens_in=200,
            tokens_out=50,
            request_kind="completion_probe",
            billable=True,
        )
        _, saved_cost = self._run(usage)
        event = saved_cost[0]
        self.assertEqual(event["request_kind"], "completion_probe")
        self.assertEqual(event["input_price_per_1m"], 0.8)
        self.assertEqual(event["output_price_per_1m"], 3.2)
        self.assertAlmostEqual(
            event["estimated_cost_usd"],
            200 / 1_000_000 * 0.8 + 50 / 1_000_000 * 3.2,
        )

    def test_missing_price_makes_billable_event_incomplete(self) -> None:
        usage = SimpleNamespace(
            tokens_in=200,
            tokens_out=50,
            request_kind="completion_probe",
            billable=True,
        )
        with mock.patch.object(
            run_tokenizer_ledger,
            "prices_for_api_model",
            return_value=(None, None),
        ):
            _, saved_cost = self._run(usage)
        event = saved_cost[0]
        self.assertIsNone(event["estimated_cost_usd"])
        self.assertFalse(event["complete"])

    def test_failed_rerun_clears_group_and_emits_no_event(self) -> None:
        existing = [
            {
                "event_id": "old-ledger",
                "date": "2026-09-03",
                "run_at": "2026-09-03T11:00:00Z",
                "source": "ledger",
                "provider_id": "aws",
                "tier": "flagship",
                "task_id": "D",
                "turn": None,
                "request_kind": "count_endpoint",
                "api_model": "amazon.nova-premier-v1:0",
                "input_tokens": 10,
                "output_tokens": 0,
                "input_price_per_1m": 2.5,
                "output_price_per_1m": 12.5,
                "input_cost_usd": 0.0,
                "output_cost_usd": 0.0,
                "estimated_cost_usd": 0.0,
                "pricing_snapshot_date": "2026-09-01",
                "corpus_version": "3.0.0",
                "chat_corpus_version": None,
                "run_id": "old",
                "replicate": 1,
                "attempt": 1,
                "canonical": True,
                "complete": True,
            },
            {
                "event_id": "keep-meter",
                "date": "2026-09-03",
                "run_at": "2026-09-03T11:00:00Z",
                "source": "meter",
                "provider_id": "aws",
                "tier": "flagship",
                "task_id": "D",
                "turn": None,
                "request_kind": "generation",
                "api_model": "amazon.nova-pro-v1:0",
                "input_tokens": 33,
                "output_tokens": 44,
                "input_price_per_1m": 0.8,
                "output_price_per_1m": 3.2,
                "input_cost_usd": 0.0,
                "output_cost_usd": 0.0,
                "estimated_cost_usd": 0.0,
                "pricing_snapshot_date": "2026-09-01",
                "corpus_version": "3.0.0",
                "chat_corpus_version": None,
                "run_id": "keep",
                "replicate": 1,
                "attempt": 1,
                "canonical": True,
                "complete": True,
            },
        ]
        ledger_rows: list[list[dict]] = []
        cost_rows: list[list[dict]] = []
        with (
            mock.patch.object(
                sys,
                "argv",
                ["run_tokenizer_ledger.py", "--tasks", "D", "--date", "2026-09-03"],
            ),
            mock.patch.object(
                run_tokenizer_ledger,
                "load_equivalence",
                return_value={
                    "selected_models_by_mode": {"two": [self.MODEL]},
                    "pricing_snapshot_date": "2026-09-01",
                },
            ),
            mock.patch.object(run_tokenizer_ledger, "load_panel", return_value=[self.MODEL]),
            mock.patch.object(run_tokenizer_ledger, "load_ledger", return_value=[]),
            mock.patch.object(run_tokenizer_ledger, "save_ledger", side_effect=ledger_rows.append),
            mock.patch.object(run_tokenizer_ledger, "load_cost_events", return_value=existing),
            mock.patch.object(run_tokenizer_ledger, "save_cost_events", side_effect=cost_rows.append),
            mock.patch.object(run_tokenizer_ledger, "env_for_provider", return_value="key"),
            mock.patch.object(
                run_tokenizer_ledger,
                "count_prompt_tokens_text",
                return_value=("error", None, "boom", "amazon.nova-pro-v1:0"),
            ),
            mock.patch.object(run_tokenizer_ledger, "now_iso_z", return_value="2026-09-03T12:00:00Z"),
        ):
            self.assertEqual(run_tokenizer_ledger.main(), 0)
        self.assertEqual(len(cost_rows), 1)
        saved = cost_rows[0]
        self.assertFalse(
            any(
                row.get("source") == "ledger"
                and row.get("date") == "2026-09-03"
                and row.get("provider_id") == "aws"
                and row.get("tier") == "flagship"
                and row.get("task_id") == "D"
                for row in saved
            )
        )
        self.assertTrue(any(row.get("event_id") == "keep-meter" for row in saved))


class GoogleLedgerCostEventsTest(unittest.TestCase):
    MODEL = {
        "provider_id": "google",
        "tier": "flagship",
        "model_id": "gemini-3.1-pro",
        "input_price": 1.25,
        "output_price": 7.5,
        "api_candidates": [],
    }

    def _run(self, usage: SimpleNamespace, api_model: str) -> dict:
        cost_rows: list[list[dict]] = []
        with (
            mock.patch.object(
                sys,
                "argv",
                ["run_tokenizer_ledger.py", "--tasks", "D", "--date", "2026-09-03"],
            ),
            mock.patch.object(
                run_tokenizer_ledger,
                "load_equivalence",
                return_value={
                    "selected_models_by_mode": {"two": [self.MODEL]},
                    "pricing_snapshot_date": "2026-09-01",
                },
            ),
            mock.patch.object(run_tokenizer_ledger, "load_panel", return_value=[self.MODEL]),
            mock.patch.object(run_tokenizer_ledger, "load_ledger", return_value=[]),
            mock.patch.object(run_tokenizer_ledger, "save_ledger"),
            mock.patch.object(run_tokenizer_ledger, "load_cost_events", return_value=[]),
            mock.patch.object(run_tokenizer_ledger, "save_cost_events", side_effect=cost_rows.append),
            mock.patch.object(run_tokenizer_ledger, "env_for_provider", return_value="key"),
            mock.patch.object(
                run_tokenizer_ledger,
                "count_prompt_tokens_text",
                return_value=("ok", usage, None, api_model),
            ),
            mock.patch.object(run_tokenizer_ledger, "now_iso_z", return_value="2026-09-03T12:00:00Z"),
        ):
            self.assertEqual(run_tokenizer_ledger.main(), 0)
        return cost_rows[0][0]

    def test_google_native_count_on_live_id_is_complete_zero_cost(self) -> None:
        event = self._run(
            SimpleNamespace(
                tokens_in=120,
                tokens_out=0,
                request_kind="count_endpoint",
                billable=False,
            ),
            "gemini-pro-latest",
        )
        self.assertEqual(event["input_price_per_1m"], 1.25)
        self.assertEqual(event["output_price_per_1m"], 7.5)
        self.assertEqual(event["estimated_cost_usd"], 0.0)
        self.assertTrue(event["complete"])

    def test_google_generation_fallback_on_live_id_is_complete_and_costed(self) -> None:
        event = self._run(
            SimpleNamespace(
                tokens_in=200,
                tokens_out=50,
                request_kind="completion_probe",
                billable=True,
            ),
            "gemini-flash-latest-high-res-exp",
        )
        self.assertEqual(event["input_price_per_1m"], 1.25)
        self.assertEqual(event["output_price_per_1m"], 7.5)
        self.assertAlmostEqual(
            event["estimated_cost_usd"],
            200 / 1_000_000 * 1.25 + 50 / 1_000_000 * 7.5,
        )
        self.assertTrue(event["complete"])


class TaskSetTest(unittest.TestCase):
    def test_all_tracks_the_corpus(self) -> None:
        self.assertEqual(run_tokenizer_ledger.TASK_SETS["all"], list(LEDGER_TASK_IDS))
        self.assertIn("F", run_tokenizer_ledger.TASK_SETS["all"])
        self.assertNotIn("E", run_tokenizer_ledger.TASK_SETS["all"])


class WorkflowContractTest(unittest.TestCase):
    def test_daily_equivalence_collects_meter_without_wrapper_step(self) -> None:
        workflow = (
            Path(__file__).resolve().parent.parent
            / ".github"
            / "workflows"
            / "daily-token-equivalence.yml"
        ).read_text()
        self.assertIn(
            "python scripts/run_equivalence_tasks.py --mode two --workhorse-replicates 1",
            workflow,
        )
        self.assertNotIn("run_wrapper_overhead.py", workflow)

    def test_daily_equivalence_verifies_only_meter_and_ledger(self) -> None:
        workflow = (
            Path(__file__).resolve().parent.parent
            / ".github"
            / "workflows"
            / "daily-token-equivalence.yml"
        ).read_text()
        self.assertIn("--sources meter --sources ledger", workflow.replace("\\\n", " "))
        self.assertNotIn("--sources wrapper", workflow)

    def test_meter_tasks_cover_generation_a_through_f(self) -> None:
        self.assertEqual(tuple(METER_TASK_IDS), ("A", "B", "C", "D", "E", "F"))

    def test_daily_tokenizer_ledger_uses_all_tasks(self) -> None:
        workflow = (
            Path(__file__).resolve().parent.parent
            / ".github"
            / "workflows"
            / "daily-tokenizer-ledger.yml"
        ).read_text()
        self.assertIn("Count-only ledger (tasks A/B/C/D/F)", workflow)
        self.assertIn("python scripts/run_tokenizer_ledger.py --tasks all --mode two", workflow)

    def test_default_active_sources_drop_wrapper(self) -> None:
        self.assertEqual(tuple(verify_ops.SOURCES), ("meter", "ledger"))

    def test_retry_runbook_uses_ledger_all_and_no_wrapper_health_check(self) -> None:
        ledger_cmd = runbooks._token_source_command("ledger", "openai")
        self.assertIsNotNone(ledger_cmd)
        self.assertEqual(ledger_cmd[ledger_cmd.index("--tasks") + 1], "all")
        report = {"retry_targets": [{"source": "meter", "provider_id": "openai"}]}
        ok_proc = SimpleNamespace(returncode=0, stderr="", stdout="")
        with mock.patch.object(runbooks.subprocess, "run", return_value=ok_proc) as mocked:
            runbooks.runbook_retry_token_source(report)
        verify_cmd = mocked.call_args_list[-1].args[0]
        self.assertIn("meter", verify_cmd)
        self.assertIn("ledger", verify_cmd)
        self.assertNotIn("wrapper", verify_cmd)


if __name__ == "__main__":
    unittest.main()
