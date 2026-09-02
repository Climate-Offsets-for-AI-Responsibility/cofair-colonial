#!/usr/bin/env python3
"""Unit tests for collection-health derivation and the ops gate.

The gate is the answer to how `/tokens` stayed dark: per-provider failures were
recorded as rows and the job still exited 0.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "ops"))
from build_dashboard_data import build_provider_health
from ops.verify_token_runs import verify_token_runs

PANEL = [
    {"provider_id": "google", "tier": "flagship", "model_id": "gemini-3.1-pro"},
    {"provider_id": "openai", "tier": "flagship", "model_id": "chat-latest"},
]


def _ledger(
    provider_id: str,
    status: str,
    error: str | None = None,
    date: str = "2026-08-21",
) -> dict:
    return {
        "provider_id": provider_id,
        "tier": "flagship",
        "date": date,
        "run_status": status,
        "error": error,
        "api_model": "some-model",
        "run_at": f"{date}T07:00:00Z",
    }


class BuildProviderHealthTest(unittest.TestCase):
    def test_failing_provider_is_not_reporting(self) -> None:
        health = build_provider_health(
            PANEL, [], [_ledger("google", "ok"), _ledger("openai", "error", "400 bad param")], []
        )
        by_id = {item["provider_id"]: item for item in health}
        self.assertTrue(by_id["google"]["reporting"])
        self.assertFalse(by_id["openai"]["reporting"])
        self.assertEqual(by_id["openai"]["dark_sources"], ["ledger"])
        self.assertEqual(by_id["openai"]["sources"]["ledger"]["last_error"], "400 bad param")

    def test_account_fault_is_quarantined_not_dark(self) -> None:
        health = build_provider_health(
            PANEL,
            [],
            [
                _ledger("google", "ok"),
                _ledger(
                    "openai",
                    "provider_unavailable",
                    '429 billing_not_active :: {"error":{"code":"billing_not_active"}}',
                ),
            ],
            [],
        )
        openai = next(item for item in health if item["provider_id"] == "openai")
        self.assertFalse(openai["reporting"])
        self.assertEqual(openai["dark_sources"], [])
        self.assertEqual(openai["unavailable_sources"], ["ledger"])
        self.assertEqual(openai["sources"]["ledger"]["unavailable_count"], 1)
        self.assertEqual(openai["sources"]["ledger"]["error_count"], 0)

    def test_partial_success_still_counts_as_reporting(self) -> None:
        health = build_provider_health(
            PANEL, [], [_ledger("google", "ok"), _ledger("google", "error", "flake")], []
        )
        google = next(item for item in health if item["provider_id"] == "google")
        self.assertTrue(google["reporting"])
        self.assertEqual(google["dark_sources"], [])

    def test_a_new_failure_is_not_masked_by_older_successes(self) -> None:
        # Append-only artifacts keep last week's good rows forever. Health must
        # read the latest collection or a regression never surfaces.
        health = build_provider_health(
            PANEL,
            [],
            [
                _ledger("google", "ok"),
                _ledger("openai", "ok", date="2026-08-20"),
                _ledger("openai", "error", "404 no model"),
            ],
            [],
        )
        openai = next(item for item in health if item["provider_id"] == "openai")
        self.assertFalse(openai["reporting"])
        self.assertEqual(openai["sources"]["ledger"]["latest_observed"], "2026-08-21")
        self.assertEqual(openai["sources"]["ledger"]["last_ok"], "2026-08-20")

    def test_dry_runs_do_not_count_as_collection(self) -> None:
        health = build_provider_health(PANEL, [], [_ledger("google", "dry_run")], [])
        google = next(item for item in health if item["provider_id"] == "google")
        self.assertFalse(google["reporting"])
        self.assertEqual(google["sources"]["ledger"]["ok_count"], 0)
        self.assertEqual(google["sources"]["ledger"]["error_count"], 0)


class VerifyTokenRunsTest(unittest.TestCase):
    def _write(self, payload: dict) -> Path:
        tmp = Path(tempfile.mkdtemp()) / "equivalence.json"
        tmp.write_text(json.dumps(payload))
        return tmp

    def _payload(self, ledger_rows: list[dict]) -> dict:
        return {"provider_health": {"panel": build_provider_health(PANEL, [], ledger_rows, [])}}

    def test_all_providers_reporting_passes(self) -> None:
        path = self._write(self._payload([_ledger("google", "ok"), _ledger("openai", "ok")]))
        result = verify_token_runs(["ledger"], path)
        self.assertTrue(result["passed"], result["checks"])

    def test_one_dark_provider_fails_the_gate(self) -> None:
        path = self._write(
            self._payload([_ledger("google", "ok"), _ledger("openai", "error", "404 no model")])
        )
        result = verify_token_runs(["ledger"], path)
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "failed")
        dark = next(c for c in result["checks"] if c["name"] == "ledger_no_dark_providers")
        self.assertIn("openai", dark["detail"])
        self.assertIn("404 no model", dark["detail"])

    def test_account_fault_only_exits_degraded(self) -> None:
        path = self._write(
            self._payload(
                [
                    _ledger("google", "ok"),
                    _ledger(
                        "openai",
                        "provider_unavailable",
                        'billing_not_active :: {"error":{"code":"billing_not_active"}}',
                    ),
                ]
            )
        )
        result = verify_token_runs(["ledger"], path)
        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], "degraded")
        dark = next(c for c in result["checks"] if c["name"] == "ledger_no_dark_providers")
        self.assertTrue(dark["passed"])
        self.assertEqual(len(result["unavailable"]), 1)

    def test_provider_that_never_ran_fails_the_gate(self) -> None:
        path = self._write(self._payload([_ledger("google", "ok")]))
        result = verify_token_runs(["ledger"], path)
        self.assertFalse(result["passed"])
        silent = next(c for c in result["checks"] if c["name"] == "ledger_no_silent_providers")
        self.assertIn("openai", silent["detail"])

    def test_missing_health_block_fails_rather_than_passing_blind(self) -> None:
        path = self._write({"token_runs": []})
        result = verify_token_runs(["ledger"], path)
        self.assertFalse(result["passed"])

    def test_only_requested_sources_are_gated(self) -> None:
        # The daily job runs the ledger only; a stale weekly wrapper must not
        # fail it, or the gate becomes noise and gets ignored.
        path = self._write(self._payload([_ledger("google", "ok"), _ledger("openai", "ok")]))
        self.assertTrue(verify_token_runs(["ledger"], path)["passed"])
        self.assertFalse(verify_token_runs(["ledger", "wrapper"], path)["passed"])


if __name__ == "__main__":
    unittest.main()
