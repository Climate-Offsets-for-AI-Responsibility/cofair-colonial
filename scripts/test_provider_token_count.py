#!/usr/bin/env python3
"""Unit tests for the shared provider count adapters.

These cover the three faults that took `/tokens` dark while every workflow
reported success: the corpus message shape, api.openai.com's diverged request
body, and pinned model ids that the catalog lists but the API will not serve.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provider_token_count import (
    OPENAI_BASE_URL,
    OPENAI_MIN_OUTPUT_TOKENS,
    _count_one,
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


def _json_response(status: int, payload: dict) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    response.headers["Content-Type"] = "application/json"
    return response


class UncappedBodyTest(unittest.TestCase):
    """Output policy 4.0.0 sends no cap; the count-only ledger still sends 1."""

    MESSAGES = [{"role": "user", "content": "hi"}]

    def test_omits_the_cap_entirely_when_none(self) -> None:
        for base in (OPENAI_BASE_URL, "https://api.x.ai/v1"):
            with self.subTest(base=base):
                body = openai_compatible_body(base, "m", self.MESSAGES, None)
                self.assertNotIn("max_tokens", body)
                self.assertNotIn("max_completion_tokens", body)

    def test_non_openai_keeps_deterministic_temperature_when_uncapped(self) -> None:
        # Dropping the cap must not drop temperature: a stochastic run would make
        # output length uncomparable day over day, which is the whole measurement.
        body = openai_compatible_body("https://api.x.ai/v1", "m", self.MESSAGES, None)
        self.assertEqual(body["temperature"], 0)

    def test_an_explicit_cap_still_applies(self) -> None:
        # The tokenizer ledger counts with a 1-token cap and must keep working.
        self.assertEqual(
            openai_compatible_body("https://api.x.ai/v1", "m", self.MESSAGES, 1)["max_tokens"], 1
        )
        self.assertEqual(
            openai_compatible_body(OPENAI_BASE_URL, "m", self.MESSAGES, 1)[
                "max_completion_tokens"
            ],
            OPENAI_MIN_OUTPUT_TOKENS,
        )


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


class CountUsageShapeTest(unittest.TestCase):
    def test_openai_completion_probe_retains_output_usage(self) -> None:
        with mock.patch(
            "provider_token_count.request_with_retry",
            return_value=_json_response(
                200,
                {"usage": {"prompt_tokens": 29, "completion_tokens": 16}},
            ),
        ):
            usage, model = _count_one("openai", "chat-latest", "hello", "key", False)
        self.assertEqual(model, "chat-latest")
        self.assertEqual(usage.request_kind, "completion_probe")
        self.assertEqual(usage.tokens_out, 16)
        self.assertTrue(usage.billable)

    def test_gemini_native_count_is_non_billable(self) -> None:
        with (
            mock.patch("provider_token_count._gemini_candidates", return_value=["gemini-pro-latest"]),
            mock.patch(
                "provider_token_count.request_with_retry",
                return_value=_json_response(200, {"promptTokenCount": 12}),
            ),
        ):
            usage, model = _count_one("google", "gemini-pro-latest", "hello", "key", False)
        self.assertEqual(model, "gemini-pro-latest")
        self.assertEqual(usage.request_kind, "count_endpoint")
        self.assertEqual(usage.tokens_out, 0)
        self.assertFalse(usage.billable)

    def test_gemini_count_fallback_to_generation_is_billable(self) -> None:
        with (
            mock.patch("provider_token_count._gemini_candidates", return_value=["gemini-pro-latest"]),
            mock.patch(
                "provider_token_count.request_with_retry",
                side_effect=[
                    _json_response(404, {"error": {"message": "not found"}}),
                    _json_response(
                        200,
                        {
                            "usageMetadata": {
                                "promptTokenCount": 12,
                                "candidatesTokenCount": 7,
                            }
                        },
                    ),
                ],
            ),
        ):
            usage, model = _count_one("google", "gemini-pro-latest", "hello", "key", False)
        self.assertEqual(model, "gemini-pro-latest")
        self.assertEqual(usage.request_kind, "completion_probe")
        self.assertEqual(usage.tokens_out, 7)
        self.assertTrue(usage.billable)


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


class ProviderFaultsTest(unittest.TestCase):
    def test_billing_not_active_is_account_not_transient(self) -> None:
        from ops.provider_faults import classify_provider_error, run_status_for_http

        body = '{"error":{"code":"billing_not_active","message":"Your account is not active"}}'
        self.assertEqual(classify_provider_error(429, body), "account")
        self.assertEqual(run_status_for_http(429, body), "provider_unavailable")


class HttpErrorPathTest(unittest.TestCase):
    """Exercise the real error path, not just the classifier behind it.

    `_status_for_http_error` called `run_status_for_http` while the module imported
    `run_status_for_category` — its sibling — so every non-retryable provider
    error raised `NameError` instead of being classified. It took the whole daily
    collection down: OpenAI's billing outage returns 429, so the first provider to
    fault killed the run before any other provider was counted.

    It survived review because the existing test imported `run_status_for_http`
    directly and asserted on it, which passes whether or not the module that needs
    it imported it. So drive the function that actually runs in production.
    """

    def _status(self, code: int, body: str) -> str:
        import provider_token_count as ptc

        response = requests.Response()
        response.status_code = code
        response._content = body.encode()
        return ptc._status_for_http_error(
            requests.HTTPError(f"{code} Client Error", response=response)
        )

    def test_billing_outage_is_classified_not_raised(self) -> None:
        body = '{"error":{"code":"billing_not_active","message":"not active"}}'
        self.assertEqual(self._status(429, body), "provider_unavailable")

    def test_ordinary_rate_limit_stays_an_error(self) -> None:
        self.assertEqual(self._status(429, '{"error":{"message":"slow down"}}'), "error")

    def test_a_missing_response_does_not_blow_up(self) -> None:
        import provider_token_count as ptc

        self.assertEqual(
            ptc._status_for_http_error(requests.HTTPError("boom", response=None)), "error"
        )

    def test_a_faulting_provider_does_not_abort_the_run(self) -> None:
        """The consequence that mattered: one provider's fault is reported, not
        raised, so the remaining providers still get counted."""
        import provider_token_count as ptc

        body = '{"error":{"code":"billing_not_active","message":"not active"}}'
        response = requests.Response()
        response.status_code = 429
        response._content = body.encode()

        with mock.patch.object(
            ptc,
            "_count_one",
            side_effect=requests.HTTPError("429 Client Error", response=response),
        ):
            status, tokens, error, used = ptc._count(
                "openai", "gpt-5.6", "hello", "key", is_messages=False, candidates=None
            )

        self.assertEqual(status, "provider_unavailable")
        self.assertIsNone(tokens)
        self.assertIsNotNone(error)

    def test_http_error_details_are_redacted(self) -> None:
        import provider_token_count as ptc

        body = (
            'Authorization: Bearer sk-live-secret token=abc123 key=xyz '
            'api_key=foo x-api-key: bar sk-rawvalue '
            '{"api_key":"secret-1","token":"secret-2",'
            '"jwt":"eyJhbGciOiJIUzI1NiJ9.abc.def",'
            '"google":"AIzaSyD123456789012345678901234567890123",'
            '"aws":"AKIA1234567890ABCD12"}'
        )
        response = _json_response(401, {"error": body})
        response._content = body.encode()
        with mock.patch.object(
            ptc,
            "_count_one",
            side_effect=requests.HTTPError("401 Client Error", response=response),
        ):
            status, _, error, _ = ptc._count(
                "openai", "gpt-5.6", "hello", "key", is_messages=False, candidates=None
            )

        self.assertEqual(status, "error")
        self.assertIsNotNone(error)
        redacted = error or ""
        for secret in (
            "sk-live-secret",
            "abc123",
            "xyz",
            "foo",
            "bar",
            "sk-rawvalue",
            "secret-1",
            "secret-2",
            "AIzaSyD123456789012345678901234567890123",
            "AKIA1234567890ABCD12",
            "eyJhbGciOiJIUzI1NiJ9.abc.def",
        ):
            self.assertNotIn(secret, redacted)
        self.assertIn("Bearer [REDACTED]", redacted)

    def test_generic_error_details_are_redacted(self) -> None:
        import provider_token_count as ptc

        with mock.patch.object(
            ptc,
            "_count_one",
            side_effect=RuntimeError(
                'boom {"api_key":"secret","token":"abc","jwt":"eyJhbGciOiJIUzI1NiJ9.a.b"} '
                "Bearer sk-secret AIzaSyD123456789012345678901234567890123 AKIA1234567890ABCD12"
            ),
        ):
            status, _, error, _ = ptc._count(
                "openai", "gpt-5.6", "hello", "key", is_messages=False, candidates=None
            )
        self.assertEqual(status, "error")
        redacted = error or ""
        self.assertNotIn("secret", redacted)
        self.assertNotIn("abc", redacted)
        self.assertNotIn("sk-secret", redacted)
        self.assertNotIn("AIzaSyD123456789012345678901234567890123", redacted)
        self.assertNotIn("AKIA1234567890ABCD12", redacted)


class GeminiTierResolutionTest(unittest.TestCase):
    """Gemini must count a flagship row on a flagship model.

    Gemini is the only provider whose callable id is discovered at runtime. The
    panel pins ids from the price catalog — `gemini-3.1-pro` — that
    `/v1beta/models` does not serve, so the preferred id misses and the tier hint
    is the only thing left to choose with. `_count_one` used to hard-code that
    hint per payload shape (workhorse for text, flagship for messages), which
    silently counted google flagship on gemini-flash-latest for the entire
    tokenizer ledger: one model reported under two tiers, and a fabricated
    agreement between them.
    """

    LIVE = ["gemini-pro-latest", "gemini-flash-latest"]

    def _resolved(self, tier: str | None, is_messages: bool = False) -> str:
        import provider_token_count as ptc

        seen: dict[str, str] = {}

        def fake_candidates(api_key, tier_hint, preferred):
            seen["tier_hint"] = tier_hint
            ordered = []
            if preferred in self.LIVE:
                ordered.append(preferred)
            if tier_hint == "flagship":
                ordered += [n for n in self.LIVE if "pro" in n and n not in ordered]
            ordered += [n for n in self.LIVE if "flash" in n and n not in ordered]
            return ordered

        with (
            mock.patch.object(ptc, "_gemini_candidates", fake_candidates),
            mock.patch.object(
                ptc,
                "_gemini_count_tokens",
                return_value=ptc.CountUsage(4031, 0, "count_endpoint", False),
            ),
        ):
            payload = [{"role": "user", "content": "hi"}] if is_messages else "hi"
            status, tokens, error, used = ptc._count(
                "google",
                "gemini-3.1-pro",
                payload,
                "key",
                is_messages=is_messages,
                candidates=None,
                tier=tier,
            )
        self.assertEqual(status, "ok", error)
        self.assertEqual(tokens, 4031)
        return used

    def test_flagship_text_resolves_to_a_pro_model(self) -> None:
        self.assertEqual(self._resolved("flagship"), "gemini-pro-latest")

    def test_workhorse_text_resolves_to_a_flash_model(self) -> None:
        self.assertEqual(self._resolved("workhorse"), "gemini-flash-latest")

    def test_the_two_tiers_do_not_collapse_onto_one_model(self) -> None:
        """The shape of the bug: both tiers reporting gemini-flash-latest."""
        self.assertNotEqual(self._resolved("flagship"), self._resolved("workhorse"))

    def test_flagship_messages_resolve_to_a_pro_model(self) -> None:
        self.assertEqual(self._resolved("flagship", is_messages=True), "gemini-pro-latest")

    def test_omitted_tier_keeps_the_prior_default(self) -> None:
        """`tier` is optional, so callers that never passed it are unchanged."""
        self.assertEqual(self._resolved(None), "gemini-flash-latest")
        self.assertEqual(self._resolved(None, is_messages=True), "gemini-pro-latest")


if __name__ == "__main__":
    unittest.main()
