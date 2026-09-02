#!/usr/bin/env python3
"""Unit tests for provider HTTP retry behavior."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provider_http import request_with_retry


class ProviderHttpRetryTest(unittest.TestCase):
    def test_retries_transient_529_then_succeeds(self) -> None:
        overloaded = MagicMock()
        overloaded.status_code = 529
        overloaded.text = '{"type":"error","error":{"type":"overloaded_error"}}'
        overloaded.headers = {}
        overloaded.ok = False

        success = MagicMock()
        success.status_code = 200
        success.text = '{"usage":{"input_tokens":1}}'
        success.headers = {}
        success.ok = True

        with patch("provider_http.requests.request", side_effect=[overloaded, success]) as mocked:
            with patch("provider_http.time.sleep"):
                response = request_with_retry("POST", "https://api.anthropic.com/v1/messages", timeout=1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked.call_count, 2)

    def test_honors_retry_after_header(self) -> None:
        transient = MagicMock()
        transient.status_code = 503
        transient.text = "overloaded"
        transient.headers = {"Retry-After": "2"}
        transient.ok = False

        success = MagicMock()
        success.status_code = 200
        success.text = "ok"
        success.headers = {}
        success.ok = True

        with patch("provider_http.requests.request", side_effect=[transient, success]):
            with patch("provider_http.time.sleep") as slept:
                response = request_with_retry("GET", "https://example.com", timeout=1)
        self.assertEqual(response.status_code, 200)
        slept.assert_called_once_with(2)

    def test_does_not_retry_billing_not_active(self) -> None:
        billing = MagicMock()
        billing.status_code = 429
        billing.text = (
            '{"error":{"message":"Your account is not active","code":"billing_not_active"}}'
        )
        billing.headers = {}
        billing.ok = False

        with patch("provider_http.requests.request", return_value=billing) as mocked:
            with patch("provider_http.time.sleep") as slept:
                response = request_with_retry("POST", "https://api.openai.com/v1/chat/completions")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(mocked.call_count, 1)
        slept.assert_not_called()


if __name__ == "__main__":
    unittest.main()
