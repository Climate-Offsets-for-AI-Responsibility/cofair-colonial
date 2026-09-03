#!/usr/bin/env python3
"""Tests for the tokenizer-ledger runner's write behaviour.

Both cases here are about the ledger file being the only record of a day's
counts. Rows are keyed by (date, provider, tier, task), and a run *replaces*
matching keys, so anything that writes rows it did not really collect destroys
the real ones.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_tokenizer_ledger
from task_corpus import LEDGER_TASK_IDS

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
        with (
            mock.patch.object(sys, "argv", ["run_tokenizer_ledger.py", *argv]),
            mock.patch.object(run_tokenizer_ledger, "load_panel", return_value=PANEL),
            mock.patch.object(
                run_tokenizer_ledger, "load_ledger", return_value=list(COLLECTED)
            ),
            mock.patch.object(
                run_tokenizer_ledger, "save_ledger", side_effect=written.append
            ),
            mock.patch.object(run_tokenizer_ledger, "env_for_provider", return_value=None),
        ):
            self.assertEqual(run_tokenizer_ledger.main(), 0)
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


class TaskSetTest(unittest.TestCase):
    def test_all_tracks_the_corpus(self) -> None:
        self.assertEqual(run_tokenizer_ledger.TASK_SETS["all"], list(LEDGER_TASK_IDS))
        self.assertIn("F", run_tokenizer_ledger.TASK_SETS["all"])
        self.assertNotIn("E", run_tokenizer_ledger.TASK_SETS["all"])


if __name__ == "__main__":
    unittest.main()
