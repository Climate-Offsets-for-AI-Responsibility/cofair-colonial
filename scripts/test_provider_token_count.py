#!/usr/bin/env python3
"""Unit tests for the shared provider count adapters.

These cover the three faults that took `/tokens` dark while every workflow
reported success: the corpus message shape, api.openai.com's diverged request
body, and pinned model ids that the catalog lists but the API will not serve.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provider_token_count import (
    OPENAI_BASE_URL,
    OPENAI_MIN_OUTPUT_TOKENS,
    _is_model_unavailable,
    api_model_candidates,
    normalize_messages,
    openai_compatible_body,
)


def _http_error(status: int, body: str) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    response._content = body.encode()
    return requests.HTTPError(f"{status} Client Error", response=response)


class NormalizeMessagesTest(unittest.TestCase):
    def test_corpus_text_turns_become_content(self) -> None:
        # CHAT_TRANSCRIPT stores {role, text}; every chat endpoint but Bedrock
        # and Gemini rejects that shape outright.
        self.assertEqual(
            normalize_messages([{"role": "assistant", "text": "hi"}]),
            [{"role": "assistant", "content": "hi"}],
        )

    def test_content_turns_pass_through(self) -> None:
        self.assertEqual(
            normalize_messages([{"role": "user", "content": "hi"}]),
            [{"role": "user", "content": "hi"}],
        )

    def test_missing_body_becomes_empty_string_not_none(self) -> None:
        self.assertEqual(normalize_messages([{"role": "user"}]), [{"role": "user", "content": ""}])


class OpenAICompatibleBodyTest(unittest.TestCase):
    def test_openai_uses_max_completion_tokens_and_no_temperature(self) -> None:
        body = openai_compatible_body(OPENAI_BASE_URL, "chat-latest", [], 300)
        self.assertEqual(body["max_completion_tokens"], 300)
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("temperature", body)

    def test_openai_count_cap_is_raised_off_one(self) -> None:
        # A cap of 1 is refused outright ("could not finish the message"), which
        # broke every OpenAI ledger and wrapper count.
        body = openai_compatible_body(OPENAI_BASE_URL, "chat-latest", [], 1)
        self.assertEqual(body["max_completion_tokens"], OPENAI_MIN_OUTPUT_TOKENS)

    def test_other_providers_keep_max_tokens_and_temperature(self) -> None:
        body = openai_compatible_body("https://api.x.ai/v1", "grok-4.6", [], 1)
        self.assertEqual(body["max_tokens"], 1)
        self.assertEqual(body["temperature"], 0)
        self.assertNotIn("max_completion_tokens", body)


class ApiModelCandidatesTest(unittest.TestCase):
    def test_bedrock_falls_through_the_tier_list_in_order(self) -> None:
        self.assertEqual(
            api_model_candidates(
                "aws", "nova-premier", "flagship", None, ["nova-2.0-pro", "nova-pro"]
            ),
            [
                "us.amazon.nova-premier-v1:0",
                "us.amazon.nova-2-pro-v1:0",
                "amazon.nova-pro-v1:0",
            ],
        )

    def test_priced_candidate_rows_are_accepted(self) -> None:
        self.assertEqual(
            api_model_candidates(
                "aws", "nova-micro", "workhorse", None, [{"model_id": "nova-lite"}]
            ),
            ["amazon.nova-micro-v1:0", "amazon.nova-lite-v1:0"],
        )

    def test_qwen_ids_are_mapped(self) -> None:
        self.assertEqual(
            api_model_candidates("qwen", "qwen3.7-max", "flagship", None), ["qwen-max"]
        )

    def test_anthropic_dots_become_dashes_without_a_key(self) -> None:
        self.assertEqual(
            api_model_candidates("anthropic", "claude-haiku-4.5", "workhorse", None),
            ["claude-haiku-4-5"],
        )

    def test_duplicate_candidates_are_collapsed(self) -> None:
        self.assertEqual(
            api_model_candidates("xai", "grok-4.6", "flagship", None, ["grok-4.6", "grok-4.5"]),
            ["grok-4.6", "grok-4.5"],
        )


class ModelUnavailableTest(unittest.TestCase):
    def test_404_and_403_are_retryable(self) -> None:
        self.assertTrue(_is_model_unavailable(_http_error(404, "not found")))
        self.assertTrue(_is_model_unavailable(_http_error(403, "denied")))

    def test_bedrock_legacy_denial_is_retryable(self) -> None:
        self.assertTrue(
            _is_model_unavailable(
                _http_error(400, '{"message":"Access denied. This Model is marked as Legacy"}')
            )
        )

    def test_invalid_model_identifier_is_retryable(self) -> None:
        self.assertTrue(
            _is_model_unavailable(_http_error(400, '{"message":"The provided model identifier is invalid."}'))
        )

    def test_parameter_error_is_not_retried_across_candidates(self) -> None:
        # Retrying this seven times would have hidden the real OpenAI defect
        # behind a pile of identical failures.
        body = (
            '{"error":{"message":"Unsupported parameter: \'max_tokens\' is not supported with '
            "this model. Use 'max_completion_tokens' instead.\",\"param\":\"max_tokens\"}}"
        )
        self.assertFalse(_is_model_unavailable(_http_error(400, body)))

    def test_missing_response_is_not_retryable(self) -> None:
        self.assertFalse(_is_model_unavailable(requests.HTTPError("boom")))


if __name__ == "__main__":
    unittest.main()
