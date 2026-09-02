#!/usr/bin/env python3
"""Unit tests for the uncapped output policy (4.0.0).

Two things changed together and neither works without the other: the runner stops
sending an output cap, and it starts reading each provider's own stop reason. The
old `tokens_out == cap` inference cannot survive the first change — with no cap
there is nothing to compare against — so truncation would silently become
undetectable exactly on the measure that is 80–98% of the billed tokens.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_equivalence_tasks as runner


def _response(status: int, payload: dict | None = None, text: str = "") -> mock.Mock:
    response = mock.Mock(spec=requests.Response)
    response.status_code = status
    response.ok = status < 400
    response.text = text
    response.headers = {}
    response.json.return_value = payload or {}
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(
            f"{status} Client Error", response=response
        )
    else:
        response.raise_for_status.return_value = None
    return response


ANTHROPIC_OK = {
    "usage": {"input_tokens": 59, "output_tokens": 878},
    "stop_reason": "end_turn",
}


ANTHROPIC_TRUNCATED = {
    "usage": {"input_tokens": 59, "output_tokens": 8192},
    "stop_reason": "max_tokens",
}


def _clear_anthropic_state() -> None:
    runner._ANTHROPIC_ACCEPTED_MAX.clear()
    runner._ANTHROPIC_REJECTED_MIN.clear()


def _limit_rejection(asked: int, limit: int) -> mock.Mock:
    return _response(
        400,
        text=(
            f'{{"error":{{"message":"max_tokens: {asked} > {limit}, which is the '
            f'maximum allowed number of output tokens for claude-opus-5"}}}}'
        ),
    )


class AnthropicCapDiscoveryTest(unittest.TestCase):
    """Anthropic requires `max_tokens`, so "uncapped" has to be discovered.

    The number must not bind even on a model that has not shipped yet, so nothing
    is written down: ask for more than any model allows and let the API say what
    the limit is.
    """

    def setUp(self) -> None:
        _clear_anthropic_state()

    def test_uses_the_limit_the_rejection_names(self) -> None:
        # The normal path: one absurd ask, one rejection that states the real
        # maximum, one call at exactly that maximum. No rung guessing at all.
        calls = [_limit_rejection(1_000_000, 64_000), _response(200, ANTHROPIC_OK)]
        with mock.patch.object(runner, "_http_request", side_effect=calls) as post:
            usage = runner.run_anthropic("claude-opus-5", "hi", None, "k")

        self.assertEqual(usage.cap_sent, 64_000)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], 64_000)

    def test_asks_absurdly_high_first_so_the_cap_cannot_bind(self) -> None:
        # If a future model does allow it, the first ask simply succeeds and no
        # cap was ever in play.
        with mock.patch.object(
            runner, "_http_request", return_value=_response(200, ANTHROPIC_OK)
        ) as post:
            usage = runner.run_anthropic("claude-opus-6", "hi", None, "k")

        self.assertEqual(usage.cap_sent, runner.ANTHROPIC_MAX_TOKENS_LADDER[0])
        self.assertGreaterEqual(usage.cap_sent, 1_000_000)
        self.assertEqual(post.call_count, 1)

    def test_falls_back_to_the_ladder_when_no_limit_is_named(self) -> None:
        # Covers the streaming-required refusal too, whose message mentions
        # max_tokens without naming a maximum. Stepping down is right either way.
        vague = _response(400, text="max_tokens is too large for a non-streaming request")
        calls = [vague, vague, _response(200, ANTHROPIC_OK)]
        with mock.patch.object(runner, "_http_request", side_effect=calls) as post:
            usage = runner.run_anthropic("claude-opus-5", "hi", None, "k")

        self.assertEqual(usage.cap_sent, runner.ANTHROPIC_MAX_TOKENS_LADDER[2])
        self.assertEqual(post.call_count, 3)

    def test_a_non_cap_400_is_not_retried_down_the_ladder(self) -> None:
        """Retrying an unrelated 400 would report a smaller cap as though the
        model's limit were the problem, hiding the real fault."""
        with mock.patch.object(
            runner, "_http_request", return_value=_response(400, text="invalid api key")
        ) as post:
            with self.assertRaises(requests.HTTPError):
                runner.run_anthropic("claude-opus-5", "hi", None, "k")

        self.assertEqual(post.call_count, 1)

    def test_caches_the_discovered_limit_for_the_rest_of_the_run(self) -> None:
        """A rejected request is not billed, but 56 rows of re-discovery is a lot
        of pointless round trips. Discover once per model, then reuse."""
        calls = [_limit_rejection(1_000_000, 64_000), _response(200, ANTHROPIC_OK)]
        with mock.patch.object(runner, "_http_request", side_effect=calls):
            first = runner.run_anthropic("claude-opus-5", "hi", None, "k")

        with mock.patch.object(
            runner, "_http_request", return_value=_response(200, ANTHROPIC_OK)
        ) as post:
            second = runner.run_anthropic("claude-opus-5", "hi", None, "k")

        self.assertEqual(second.cap_sent, first.cap_sent)
        self.assertEqual(post.call_count, 1)

    def test_an_explicit_cap_is_never_revised(self) -> None:
        # The count-only ledger's 1-token cap is an instruction, not a guess.
        with mock.patch.object(
            runner, "_http_request", return_value=_response(200, ANTHROPIC_TRUNCATED)
        ) as post:
            usage = runner.run_anthropic("claude-opus-5", "hi", 512, "k")

        self.assertEqual(usage.cap_sent, 512)
        self.assertTrue(usage.truncated)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], 512)


class AnthropicStepUpTest(unittest.TestCase):
    """Stepping down for safety must not be a one-way ratchet.

    A cap we chose is a hint. If a reading comes back truncated at that cap, the
    hint was too low and the reading is a floor rather than a measurement — so the
    ladder has to be climbed again, not trusted.
    """

    def setUp(self) -> None:
        _clear_anthropic_state()

    def test_truncation_at_a_cached_cap_steps_back_up(self) -> None:
        # Cache says 8,192 from an earlier probe. This call hits it, which is
        # proof 8,192 is not this model's real limit.
        runner._ANTHROPIC_ACCEPTED_MAX["claude-opus-5"] = 8_192
        calls = [_response(200, ANTHROPIC_TRUNCATED), _response(200, ANTHROPIC_OK)]
        with mock.patch.object(runner, "_http_request", side_effect=calls) as post:
            usage = runner.run_anthropic("claude-opus-5", "hi", None, "k")

        self.assertEqual(post.call_count, 2)
        self.assertEqual([c.kwargs["json"]["max_tokens"] for c in post.call_args_list], [8_192, 16_384])
        self.assertEqual(usage.cap_sent, 16_384)
        self.assertFalse(usage.truncated)
        # And the cache moves up, so the rest of the run starts from the better
        # value instead of re-truncating on every remaining row.
        self.assertEqual(runner._ANTHROPIC_ACCEPTED_MAX["claude-opus-5"], 16_384)

    def test_it_keeps_climbing_while_it_keeps_truncating(self) -> None:
        runner._ANTHROPIC_ACCEPTED_MAX["claude-opus-5"] = 8_192
        calls = [
            _response(200, ANTHROPIC_TRUNCATED),
            _response(200, ANTHROPIC_TRUNCATED),
            _response(200, ANTHROPIC_TRUNCATED),
            _response(200, ANTHROPIC_OK),
        ]
        with mock.patch.object(runner, "_http_request", side_effect=calls) as post:
            usage = runner.run_anthropic("claude-opus-5", "hi", None, "k")

        self.assertEqual(
            [c.kwargs["json"]["max_tokens"] for c in post.call_args_list],
            [8_192, 16_384, 32_000, 64_000],
        )
        self.assertEqual(usage.cap_sent, 64_000)
        self.assertFalse(usage.truncated)

    def test_it_does_not_climb_into_a_limit_the_model_already_refused(self) -> None:
        # Discovery established 64,000 as the maximum. Truncation there is the
        # provider's own ceiling, so there is nothing to step up to and no point
        # spending a request to prove it.
        runner._ANTHROPIC_ACCEPTED_MAX["claude-opus-5"] = 64_000
        runner._ANTHROPIC_REJECTED_MIN["claude-opus-5"] = 64_001
        with mock.patch.object(
            runner, "_http_request", return_value=_response(200, ANTHROPIC_TRUNCATED)
        ) as post:
            usage = runner.run_anthropic("claude-opus-5", "hi", None, "k")

        self.assertEqual(post.call_count, 1)
        self.assertEqual(usage.cap_sent, 64_000)
        # Reported, not hidden: the chart draws this as a cross.
        self.assertTrue(usage.truncated)

    def test_a_truncated_reading_is_never_silently_returned_with_headroom_left(self) -> None:
        """The invariant behind both directions: if the runner returns a truncated
        row, it is because nothing higher was available — never because it settled
        for the first cap it found."""
        runner._ANTHROPIC_ACCEPTED_MAX["claude-opus-5"] = 4_096
        truncated = _response(200, ANTHROPIC_TRUNCATED)
        with mock.patch.object(runner, "_http_request", return_value=truncated) as post:
            usage = runner.run_anthropic("claude-opus-5", "hi", None, "k")

        self.assertTrue(usage.truncated)
        # Climbed every rung from 4,096 to the top before giving up.
        self.assertEqual(usage.cap_sent, runner.ANTHROPIC_MAX_TOKENS_LADDER[0])
        self.assertEqual(post.call_count, len(runner.ANTHROPIC_MAX_TOKENS_LADDER))


class TruncationFromStopReasonTest(unittest.TestCase):
    """Truncation is read, not inferred."""

    def setUp(self) -> None:
        _clear_anthropic_state()

    def test_anthropic(self) -> None:
        for reason, expected in (("max_tokens", True), ("end_turn", False)):
            with self.subTest(reason=reason):
                payload = {"usage": {"input_tokens": 1, "output_tokens": 2}, "stop_reason": reason}
                with mock.patch.object(
                    runner, "_http_request", return_value=_response(200, payload)
                ):
                    self.assertIs(
                        runner.run_anthropic("m", "hi", 10, "k").truncated, expected
                    )

    def test_openai_compatible(self) -> None:
        for reason, expected in (("length", True), ("stop", False)):
            with self.subTest(reason=reason):
                payload = {
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                    "choices": [{"finish_reason": reason}],
                }
                with mock.patch.object(
                    runner, "_http_request", return_value=_response(200, payload)
                ):
                    usage = runner.run_openai_compatible(
                        "https://api.x.ai/v1", "m", "hi", None, "k"
                    )
                self.assertIs(usage.truncated, expected)

    def test_gemini(self) -> None:
        for reason, expected in (("MAX_TOKENS", True), ("STOP", False)):
            with self.subTest(reason=reason):
                payload = {
                    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2},
                    "candidates": [{"finishReason": reason}],
                }
                with mock.patch.object(
                    runner, "_http_request", return_value=_response(200, payload)
                ):
                    self.assertIs(runner.run_gemini("m", "hi", None, "k").truncated, expected)

    def test_bedrock(self) -> None:
        for reason, expected in (("max_tokens", True), ("end_turn", False)):
            with self.subTest(reason=reason):
                payload = {
                    "usage": {"inputTokens": 1, "outputTokens": 2},
                    "stopReason": reason,
                }
                with mock.patch.object(
                    runner, "_http_request", return_value=_response(200, payload)
                ):
                    self.assertIs(runner.run_bedrock("m", "hi", None, "k").truncated, expected)

    def test_a_long_run_that_stopped_naturally_is_not_censored(self) -> None:
        """The case the old inference got wrong once the cap moved: a model that
        emits a great many tokens and then finishes is a reading, not a floor."""
        payload = {
            "usage": {"prompt_tokens": 90, "completion_tokens": 31_500},
            "choices": [{"finish_reason": "stop"}],
        }
        with mock.patch.object(runner, "_http_request", return_value=_response(200, payload)):
            usage = runner.run_openai_compatible("https://api.x.ai/v1", "m", "hi", None, "k")

        self.assertEqual(usage.tokens_out, 31_500)
        self.assertFalse(usage.truncated)


class UncappedRequestBodyTest(unittest.TestCase):
    """No cap means the parameter is absent, not set to something large."""

    def test_gemini_omits_max_output_tokens(self) -> None:
        payload = {
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2},
            "candidates": [{"finishReason": "STOP"}],
        }
        with mock.patch.object(
            runner, "_http_request", return_value=_response(200, payload)
        ) as post:
            runner.run_gemini("m", "hi", None, "k")

        config = post.call_args.kwargs["json"]["generationConfig"]
        self.assertNotIn("maxOutputTokens", config)
        self.assertEqual(config["temperature"], 0)

    def test_bedrock_omits_max_tokens(self) -> None:
        payload = {"usage": {"inputTokens": 1, "outputTokens": 2}, "stopReason": "end_turn"}
        with mock.patch.object(
            runner, "_http_request", return_value=_response(200, payload)
        ) as post:
            runner.run_bedrock("m", "hi", None, "k")

        config = post.call_args.kwargs["json"]["inferenceConfig"]
        self.assertNotIn("maxTokens", config)
        self.assertEqual(config["temperature"], 0)


if __name__ == "__main__":
    unittest.main()
