#!/usr/bin/env python3
"""Ops classifier + runbook routing for colonial pipelines."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OPS))

from incident import classify_error
from runbooks import execute_runbook

import scrape_pricing as scrape


DEEPSEEK_TIMEOUT = (
    "Failed after 3 attempts: HTTPSConnectionPool(host='api-docs.deepseek.com', "
    "port=443): Max retries exceeded with url: /quick_start/pricing (Caused by "
    "ConnectTimeoutError(<HTTPSConnection(host='api-docs.deepseek.com', port=443) "
    "at 0x7f2e854f9390>, 'Connection to api-docs.deepseek.com timed out. "
    "(connect timeout=60)'))"
)

NETWORK_RETRY = (
    "Failed after 3 attempts: HTTPSConnectionPool(host='api-docs.deepseek.com', "
    "port=443): Max retries exceeded with url: /quick_start/pricing "
    "(Caused by NewConnectionError('Connection refused'))"
)


class ClassifyOpsErrorTest(unittest.TestCase):
    def test_deepseek_connect_timeout_is_timeout(self):
        signature, category = scrape.classify_ops_error(DEEPSEEK_TIMEOUT)
        self.assertEqual(signature, "Timeout")
        self.assertEqual(category, "timeout")
        self.assertEqual(classify_error(DEEPSEEK_TIMEOUT), (signature, category))

    def test_exhausted_retries_without_timeout_is_network(self):
        signature, category = scrape.classify_ops_error(NETWORK_RETRY)
        self.assertEqual(signature, "NetworkRetryExhausted")
        self.assertEqual(category, "network")


class ExecuteRunbookRoutingTest(unittest.TestCase):
    def test_timeout_retries_scrape(self):
        with mock.patch(
            "runbooks.runbook_retry_scrape",
            return_value=(True, ["retry_scrape"], 1),
        ) as retry:
            ok, actions, tier = execute_runbook("Timeout")
        retry.assert_called_once_with()
        self.assertTrue(ok)
        self.assertEqual(actions, ["retry_scrape"])
        self.assertEqual(tier, 1)

    def test_network_retry_exhausted_retries_scrape(self):
        with mock.patch(
            "runbooks.runbook_retry_scrape",
            return_value=(True, ["retry_scrape"], 1),
        ) as retry:
            ok, actions, _tier = execute_runbook("NetworkRetryExhausted")
        retry.assert_called_once_with()
        self.assertTrue(ok)

    def test_unknown_error_still_has_no_runbook(self):
        ok, actions, tier = execute_runbook("UnknownError")
        self.assertFalse(ok)
        self.assertEqual(actions, ["no_runbook_for_signature"])
        self.assertEqual(tier, 0)


if __name__ == "__main__":
    unittest.main()
