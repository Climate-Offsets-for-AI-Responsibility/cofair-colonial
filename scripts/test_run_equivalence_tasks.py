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


def _openai_response(assistant_text: str, tokens_in: int, tokens_out: int) -> mock.Mock:
    return _response(
        200,
        {
            "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
            "choices": [{"finish_reason": "stop", "message": {"content": assistant_text}}],
        },
    )


def _anthropic_response(assistant_text: str, tokens_in: int, tokens_out: int) -> mock.Mock:
    return _response(
        200,
        {
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": assistant_text}],
        },
    )


def _gemini_response(assistant_text: str, tokens_in: int, tokens_out: int) -> mock.Mock:
    return _response(
        200,
        {
            "usageMetadata": {"promptTokenCount": tokens_in, "candidatesTokenCount": tokens_out},
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": assistant_text}]}}],
        },
    )


def _bedrock_response(assistant_text: str, tokens_in: int, tokens_out: int) -> mock.Mock:
    return _response(
        200,
        {
            "usage": {"inputTokens": tokens_in, "outputTokens": tokens_out},
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": assistant_text}]}},
        },
    )


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

    def test_message_array_ladder_preserves_full_history(self) -> None:
        messages = [
            {"role": "user", "content": "first user"},
            {"role": "assistant", "content": "first assistant"},
            {"role": "user", "content": "second user"},
        ]
        calls = [_limit_rejection(1_000_000, 64_000), _response(200, ANTHROPIC_OK)]
        with mock.patch.object(runner, "_http_request", side_effect=calls) as post:
            runner.run_anthropic("claude-opus-5", messages, None, "k")

        self.assertEqual(post.call_args_list[0].kwargs["json"]["messages"], messages)
        self.assertEqual(post.call_args_list[1].kwargs["json"]["messages"], messages)


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


class ConversationGenerationTest(unittest.TestCase):
    def setUp(self) -> None:
        _clear_anthropic_state()

    def _entry(self, provider_id: str) -> dict:
        return {
            "provider_id": provider_id,
            "model_id": f"{provider_id}-pinned",
            "tier": "workhorse",
            "input_price": 0.4,
            "output_price": 1.2,
        }

    def test_openai_conversation_keeps_prior_assistant_output(self) -> None:
        responses = [
            _openai_response("first", 10, 20),
            _openai_response("second", 30, 40),
            _openai_response("third", 50, 60),
        ]
        entry = self._entry("openai")
        with (
            mock.patch.object(runner, "env_for_provider", return_value="k"),
            mock.patch.object(
                runner, "candidate_plan", return_value=[("gpt-live", 1.1, 2.2)]
            ),
            mock.patch.object(runner, "_http_request", side_effect=responses) as request,
        ):
            result, turns = runner.run_provider_conversation(entry, None, False)

        second_messages = request.call_args_list[1].kwargs["json"]["messages"]
        self.assertEqual(
            second_messages,
            [
                {"role": "user", "content": runner.E_USER_PROMPTS[0]},
                {"role": "assistant", "content": "first"},
                {"role": "user", "content": runner.E_USER_PROMPTS[1]},
            ],
        )
        self.assertEqual((result.tokens_in, result.tokens_out), (90, 120))
        self.assertEqual([turn.turn for turn in turns], [1, 2, 3])

    def test_mid_conversation_model_unavailable_fallback_preserves_successful_turns(self) -> None:
        responses = [
            _openai_response("old-first", 10, 20),
            _response(404, text='{"error":"model not found"}'),
            _openai_response("new-first", 11, 21),
            _openai_response("new-second", 31, 41),
            _openai_response("new-third", 51, 61),
        ]
        entry = self._entry("openai")
        with (
            mock.patch.object(runner, "env_for_provider", return_value="k"),
            mock.patch.object(
                runner,
                "candidate_plan",
                return_value=[("old-model", 0.5, 1.5), ("new-model", 1.1, 2.2)],
            ),
            mock.patch.object(runner, "_http_request", side_effect=responses) as request,
        ):
            result, turns = runner.run_provider_conversation(entry, None, False)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.api_model, "new-model")
        self.assertEqual((result.tokens_in, result.tokens_out), (93, 123))
        self.assertEqual(len(turns), 4)
        self.assertEqual([turn.api_model for turn in turns], ["old-model", "new-model", "new-model", "new-model"])
        self.assertEqual(
            request.call_args_list[2].kwargs["json"]["messages"],
            [{"role": "user", "content": runner.E_USER_PROMPTS[0]}],
        )

    def test_empty_assistant_output_stops_conversation_without_next_request(self) -> None:
        entry = self._entry("openai")
        with (
            mock.patch.object(runner, "env_for_provider", return_value="k"),
            mock.patch.object(
                runner, "candidate_plan", return_value=[("gpt-live", 1.1, 2.2)]
            ),
            mock.patch.object(
                runner, "_http_request", side_effect=[_openai_response("", 10, 20)]
            ) as request,
        ):
            result, turns = runner.run_provider_conversation(entry, None, False)

        self.assertEqual(result.status, "error")
        self.assertIn("empty assistant output", (result.error or "").lower())
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(turns), 1)
        self.assertTrue(all(turn.input_price == 1.1 for turn in turns))
        self.assertTrue(all(turn.output_price == 2.2 for turn in turns))

    def test_anthropic_conversation_keeps_native_messages(self) -> None:
        responses = [
            _anthropic_response("first", 10, 20),
            _anthropic_response("second", 30, 40),
            _anthropic_response("third", 50, 60),
        ]
        entry = self._entry("anthropic")
        with (
            mock.patch.object(runner, "env_for_provider", return_value="k"),
            mock.patch.object(
                runner, "candidate_plan", return_value=[("claude-live", 1.1, 2.2)]
            ),
            mock.patch.object(runner, "_http_request", side_effect=responses) as request,
        ):
            result, turns = runner.run_provider_conversation(entry, None, False)

        second_messages = request.call_args_list[1].kwargs["json"]["messages"]
        self.assertEqual(
            second_messages,
            [
                {"role": "user", "content": runner.E_USER_PROMPTS[0]},
                {"role": "assistant", "content": "first"},
                {"role": "user", "content": runner.E_USER_PROMPTS[1]},
            ],
        )
        self.assertEqual((result.tokens_in, result.tokens_out), (90, 120))
        self.assertEqual([turn.turn for turn in turns], [1, 2, 3])

    def test_gemini_conversation_uses_user_model_content_parts(self) -> None:
        responses = [
            _gemini_response("first", 10, 20),
            _gemini_response("second", 30, 40),
            _gemini_response("third", 50, 60),
        ]
        entry = self._entry("google")
        with (
            mock.patch.object(runner, "env_for_provider", return_value="k"),
            mock.patch.object(
                runner, "candidate_plan", return_value=[("gemini-live", 1.1, 2.2)]
            ),
            mock.patch.object(runner, "_http_request", side_effect=responses) as request,
        ):
            result, turns = runner.run_provider_conversation(entry, None, False)

        second_contents = request.call_args_list[1].kwargs["json"]["contents"]
        self.assertEqual(
            second_contents,
            [
                {"role": "user", "parts": [{"text": runner.E_USER_PROMPTS[0]}]},
                {"role": "model", "parts": [{"text": "first"}]},
                {"role": "user", "parts": [{"text": runner.E_USER_PROMPTS[1]}]},
            ],
        )
        self.assertEqual((result.tokens_in, result.tokens_out), (90, 120))
        self.assertEqual([turn.turn for turn in turns], [1, 2, 3])

    def test_bedrock_conversation_uses_content_arrays(self) -> None:
        responses = [
            _bedrock_response("first", 10, 20),
            _bedrock_response("second", 30, 40),
            _bedrock_response("third", 50, 60),
        ]
        entry = self._entry("aws")
        with (
            mock.patch.object(runner, "env_for_provider", return_value="k"),
            mock.patch.object(
                runner, "candidate_plan", return_value=[("nova-live", 1.1, 2.2)]
            ),
            mock.patch.object(runner, "_http_request", side_effect=responses) as request,
        ):
            result, turns = runner.run_provider_conversation(entry, None, False)

        second_messages = request.call_args_list[1].kwargs["json"]["messages"]
        self.assertEqual(
            second_messages,
            [
                {"role": "user", "content": [{"text": runner.E_USER_PROMPTS[0]}]},
                {"role": "assistant", "content": [{"text": "first"}]},
                {"role": "user", "content": [{"text": runner.E_USER_PROMPTS[1]}]},
            ],
        )
        self.assertEqual((result.tokens_in, result.tokens_out), (90, 120))
        self.assertEqual([turn.turn for turn in turns], [1, 2, 3])


class MeterCostEventWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        _clear_anthropic_state()

    def test_dry_run_writes_neither_runs_nor_cost_events(self) -> None:
        model = {
            "provider_id": "openai",
            "model_id": "gpt-pinned",
            "tier": "workhorse",
            "input_price": 1.0,
            "output_price": 2.0,
        }
        fake_eq = {
            "pricing_snapshot_date": "2026-08-31",
            "tasks": [{"task_id": "E", "output_cap": None}],
            "selected_models_by_mode": {"two": [model], "three": [model]},
        }
        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    "run_equivalence_tasks.py",
                    "--mode",
                    "two",
                    "--date",
                    "2026-09-03",
                    "--dry-run",
                    "--workhorse-replicates",
                    "1",
                ],
            ),
            mock.patch.object(runner, "METER_TASK_IDS", ("E",)),
            mock.patch.object(runner, "load_equivalence", return_value=fake_eq),
            mock.patch.object(runner, "load_existing_runs", return_value=[]),
            mock.patch.object(
                runner,
                "run_provider_conversation",
                return_value=(
                    runner.TaskResult("dry_run", None, None, None, "gpt-live", 1.0, 2.0),
                    [],
                ),
            ),
            mock.patch.object(runner, "save_runs") as save_runs,
            mock.patch.object(runner, "save_cost_events") as save_cost_events,
        ):
            self.assertEqual(runner.main(), 0)

        save_runs.assert_not_called()
        save_cost_events.assert_not_called()

    def test_emits_real_events_for_successful_task_e_requests(self) -> None:
        model = {
            "provider_id": "openai",
            "model_id": "gpt-pinned",
            "tier": "workhorse",
            "input_price": 1.0,
            "output_price": 2.0,
        }
        fake_eq = {
            "pricing_snapshot_date": "2026-08-31",
            "tasks": [{"task_id": "E", "output_cap": None}],
            "selected_models_by_mode": {"two": [model], "three": [model]},
        }
        result = runner.TaskResult("ok", 90, 120, None, "gpt-live", 1.1, 2.2, False, None)
        turns = [
            runner.TurnResult(
                1,
                runner.Usage(10, 20, False, None, "first"),
                [{"role": "user", "content": runner.E_USER_PROMPTS[0]}],
                1.1,
                2.2,
                "gpt-live",
            ),
            runner.TurnResult(
                2,
                runner.Usage(30, 40, False, None, "second"),
                [
                    {"role": "user", "content": runner.E_USER_PROMPTS[0]},
                    {"role": "assistant", "content": "first"},
                    {"role": "user", "content": runner.E_USER_PROMPTS[1]},
                ],
                1.1,
                2.2,
                "gpt-live",
            ),
            runner.TurnResult(
                3,
                runner.Usage(50, 60, False, None, "third"),
                [
                    {"role": "user", "content": runner.E_USER_PROMPTS[0]},
                    {"role": "assistant", "content": "first"},
                    {"role": "user", "content": runner.E_USER_PROMPTS[1]},
                    {"role": "assistant", "content": "second"},
                    {"role": "user", "content": runner.E_USER_PROMPTS[2]},
                ],
                1.1,
                2.2,
                "gpt-live",
            ),
        ]

        saved_rows: list[dict] = []
        saved_runs: list[dict] = []

        def _save(rows):
            saved_rows.extend(rows)

        def _save_runs(rows):
            saved_runs.extend(rows)

        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    "run_equivalence_tasks.py",
                    "--mode",
                    "two",
                    "--date",
                    "2026-09-03",
                    "--workhorse-replicates",
                    "1",
                ],
            ),
            mock.patch.object(runner, "METER_TASK_IDS", ("E",)),
            mock.patch.object(runner, "load_equivalence", return_value=fake_eq),
            mock.patch.object(runner, "load_existing_runs", return_value=[]),
            mock.patch.object(runner, "load_cost_events", return_value=[]),
            mock.patch.object(
                runner, "run_provider_conversation", return_value=(result, turns)
            ),
            mock.patch.object(runner, "save_runs", side_effect=_save_runs),
            mock.patch.object(runner, "save_cost_events", side_effect=_save),
            mock.patch.object(runner, "now_iso_z", return_value="2026-09-03T12:00:00Z"),
        ):
            self.assertEqual(runner.main(), 0)

        self.assertEqual(len(saved_rows), 3)
        self.assertRegex(
            saved_rows[0]["event_id"],
            r"^2026-09-03:meter:openai:workhorse:E:1:generation:2026-09-03:two:1:a1:gpt-live:t1:n1:1$",
        )
        self.assertEqual([row["turn"] for row in saved_rows], [1, 2, 3])
        self.assertTrue(all(row["request_kind"] == "generation" for row in saved_rows))
        self.assertEqual(
            [row["run_id"] for row in saved_rows],
            [
                "2026-09-03:two:1:a1:gpt-live:t1:n1",
                "2026-09-03:two:1:a1:gpt-live:t2:n2",
                "2026-09-03:two:1:a1:gpt-live:t3:n3",
            ],
        )
        self.assertEqual([row["attempt"] for row in saved_rows], [1, 1, 1])
        self.assertTrue(all(row["canonical"] for row in saved_rows))
        self.assertTrue(all(row["replicate"] == 1 for row in saved_rows))
        self.assertTrue(all(row["chat_corpus_version"] == runner.CHAT_CORPUS_VERSION for row in saved_rows))
        self.assertAlmostEqual(saved_rows[0]["estimated_cost_usd"], 10 / 1_000_000 * 1.1 + 20 / 1_000_000 * 2.2)
        self.assertEqual(len(saved_runs), 1)
        expected_input_chars = sum(
            len(block["content"])
            for turn in turns
            for block in turn.messages
            if isinstance(block.get("content"), str)
        )
        self.assertEqual(saved_runs[0]["input_chars"], expected_input_chars)

    def test_non_e_single_turn_success_emits_turn_none_and_null_chat_corpus(self) -> None:
        model = {
            "provider_id": "openai",
            "model_id": "gpt-pinned",
            "tier": "flagship",
            "input_price": 1.0,
            "output_price": 2.0,
        }
        fake_eq = {
            "pricing_snapshot_date": "2026-08-31",
            "tasks": [{"task_id": "A", "output_cap": None}],
            "selected_models_by_mode": {"two": [model], "three": [model]},
        }
        saved_rows: list[dict] = []
        with (
            mock.patch.object(
                sys,
                "argv",
                ["run_equivalence_tasks.py", "--mode", "two", "--date", "2026-09-03"],
            ),
            mock.patch.object(runner, "METER_TASK_IDS", ("A",)),
            mock.patch.object(runner, "load_equivalence", return_value=fake_eq),
            mock.patch.object(runner, "load_existing_runs", return_value=[]),
            mock.patch.object(runner, "load_cost_events", return_value=[]),
            mock.patch.object(
                runner,
                "run_provider_task",
                return_value=runner.TaskResult("ok", 12, 34, None, "gpt-live", 1.1, 2.2),
            ),
            mock.patch.object(runner, "save_runs"),
            mock.patch.object(runner, "save_cost_events", side_effect=lambda rows: saved_rows.extend(rows)),
            mock.patch.object(runner, "now_iso_z", return_value="2026-09-03T12:00:00Z"),
        ):
            self.assertEqual(runner.main(), 0)

        self.assertEqual(len(saved_rows), 1)
        self.assertIsNone(saved_rows[0]["turn"])
        self.assertIsNone(saved_rows[0]["chat_corpus_version"])
        self.assertEqual(saved_rows[0]["attempt"], 1)
        self.assertTrue(saved_rows[0]["canonical"])
        self.assertEqual(saved_rows[0]["replicate"], 1)
        self.assertEqual(saved_rows[0]["pricing_snapshot_date"], "2026-08-31")

    def test_failed_non_e_task_emits_no_events(self) -> None:
        model = {
            "provider_id": "openai",
            "model_id": "gpt-pinned",
            "tier": "flagship",
            "input_price": 1.0,
            "output_price": 2.0,
        }
        fake_eq = {
            "tasks": [{"task_id": "A", "output_cap": None}],
            "selected_models_by_mode": {"two": [model], "three": [model]},
        }
        saved_rows: list[dict] = []
        with (
            mock.patch.object(
                sys,
                "argv",
                ["run_equivalence_tasks.py", "--mode", "two", "--date", "2026-09-03"],
            ),
            mock.patch.object(runner, "METER_TASK_IDS", ("A",)),
            mock.patch.object(runner, "load_equivalence", return_value=fake_eq),
            mock.patch.object(runner, "load_existing_runs", return_value=[]),
            mock.patch.object(runner, "load_cost_events", return_value=[]),
            mock.patch.object(
                runner,
                "run_provider_task",
                return_value=runner.TaskResult("error", None, None, "boom", "gpt-live", 1.1, 2.2),
            ),
            mock.patch.object(runner, "save_runs"),
            mock.patch.object(runner, "save_cost_events", side_effect=lambda rows: saved_rows.extend(rows)),
            mock.patch.object(runner, "now_iso_z", return_value="2026-09-03T12:00:00Z"),
        ):
            self.assertEqual(runner.main(), 0)
        self.assertEqual(saved_rows, [])

    def test_failed_e_task_still_emits_partial_successful_turns(self) -> None:
        model = {
            "provider_id": "openai",
            "model_id": "gpt-pinned",
            "tier": "workhorse",
            "input_price": 1.0,
            "output_price": 2.0,
        }
        fake_eq = {
            "tasks": [{"task_id": "E", "output_cap": None}],
            "selected_models_by_mode": {"two": [model], "three": [model]},
        }
        partial_turns = [
            runner.TurnResult(
                1,
                runner.Usage(10, 20, False, None, "first"),
                [{"role": "user", "content": runner.E_USER_PROMPTS[0]}],
                0.7,
                1.7,
                "old-model",
            )
        ]
        saved_rows: list[dict] = []
        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    "run_equivalence_tasks.py",
                    "--mode",
                    "two",
                    "--date",
                    "2026-09-03",
                    "--workhorse-replicates",
                    "1",
                ],
            ),
            mock.patch.object(runner, "METER_TASK_IDS", ("E",)),
            mock.patch.object(runner, "load_equivalence", return_value=fake_eq),
            mock.patch.object(runner, "load_existing_runs", return_value=[]),
            mock.patch.object(runner, "load_cost_events", return_value=[]),
            mock.patch.object(
                runner,
                "run_provider_conversation",
                return_value=(
                    runner.TaskResult("error", None, None, "mid-turn failure", "new-model", 1.1, 2.2),
                    partial_turns,
                ),
            ),
            mock.patch.object(runner, "save_runs"),
            mock.patch.object(runner, "save_cost_events", side_effect=lambda rows: saved_rows.extend(rows)),
            mock.patch.object(runner, "now_iso_z", return_value="2026-09-03T12:00:00Z"),
        ):
            self.assertEqual(runner.main(), 0)

        self.assertEqual(len(saved_rows), 1)
        self.assertEqual(saved_rows[0]["api_model"], "old-model")
        self.assertIsNone(saved_rows[0]["estimated_cost_usd"])
        self.assertFalse(saved_rows[0]["complete"])
        self.assertEqual(saved_rows[0]["attempt"], 1)
        self.assertFalse(saved_rows[0]["canonical"])
        self.assertIsNone(saved_rows[0]["pricing_snapshot_date"])

    def test_failed_e_task_without_partial_success_emits_no_events(self) -> None:
        model = {
            "provider_id": "openai",
            "model_id": "gpt-pinned",
            "tier": "workhorse",
            "input_price": 1.0,
            "output_price": 2.0,
        }
        fake_eq = {
            "pricing_snapshot_date": "2026-08-31",
            "tasks": [{"task_id": "E", "output_cap": None}],
            "selected_models_by_mode": {"two": [model], "three": [model]},
        }
        saved_rows: list[dict] = []
        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    "run_equivalence_tasks.py",
                    "--mode",
                    "two",
                    "--date",
                    "2026-09-03",
                    "--workhorse-replicates",
                    "1",
                ],
            ),
            mock.patch.object(runner, "METER_TASK_IDS", ("E",)),
            mock.patch.object(runner, "load_equivalence", return_value=fake_eq),
            mock.patch.object(runner, "load_existing_runs", return_value=[]),
            mock.patch.object(runner, "load_cost_events", return_value=[]),
            mock.patch.object(
                runner,
                "run_provider_conversation",
                return_value=(
                    runner.TaskResult("error", None, None, "fail", "new-model", 1.1, 2.2),
                    [],
                ),
            ),
            mock.patch.object(runner, "save_runs"),
            mock.patch.object(runner, "save_cost_events", side_effect=lambda rows: saved_rows.extend(rows)),
            mock.patch.object(runner, "now_iso_z", return_value="2026-09-03T12:00:00Z"),
        ):
            self.assertEqual(runner.main(), 0)
        self.assertEqual(saved_rows, [])

    def test_fallback_turn_events_keep_original_candidate_prices(self) -> None:
        model = {
            "provider_id": "openai",
            "model_id": "gpt-pinned",
            "tier": "workhorse",
            "input_price": 1.0,
            "output_price": 2.0,
        }
        fake_eq = {
            "pricing_snapshot_date": "2026-08-31",
            "tasks": [{"task_id": "E", "output_cap": None}],
            "selected_models_by_mode": {"two": [model], "three": [model]},
        }
        partial_and_final = [
            runner.TurnResult(
                1,
                runner.Usage(10, 20, False, None, "old-1"),
                [{"role": "user", "content": runner.E_USER_PROMPTS[0]}],
                0.5,
                1.5,
                "old-model",
            ),
            runner.TurnResult(
                1,
                runner.Usage(11, 21, False, None, "new-1"),
                [{"role": "user", "content": runner.E_USER_PROMPTS[0]}],
                1.1,
                2.2,
                "new-model",
            ),
            runner.TurnResult(
                2,
                runner.Usage(31, 41, False, None, "new-2"),
                [
                    {"role": "user", "content": runner.E_USER_PROMPTS[0]},
                    {"role": "assistant", "content": "new-1"},
                    {"role": "user", "content": runner.E_USER_PROMPTS[1]},
                ],
                1.1,
                2.2,
                "new-model",
            ),
            runner.TurnResult(
                3,
                runner.Usage(51, 61, False, None, "new-3"),
                [
                    {"role": "user", "content": runner.E_USER_PROMPTS[0]},
                    {"role": "assistant", "content": "new-1"},
                    {"role": "user", "content": runner.E_USER_PROMPTS[1]},
                    {"role": "assistant", "content": "new-2"},
                    {"role": "user", "content": runner.E_USER_PROMPTS[2]},
                ],
                1.1,
                2.2,
                "new-model",
            ),
        ]
        saved_rows: list[dict] = []
        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    "run_equivalence_tasks.py",
                    "--mode",
                    "two",
                    "--date",
                    "2026-09-03",
                    "--workhorse-replicates",
                    "1",
                ],
            ),
            mock.patch.object(runner, "METER_TASK_IDS", ("E",)),
            mock.patch.object(runner, "load_equivalence", return_value=fake_eq),
            mock.patch.object(runner, "load_existing_runs", return_value=[]),
            mock.patch.object(runner, "load_cost_events", return_value=[]),
            mock.patch.object(
                runner,
                "run_provider_conversation",
                return_value=(
                    runner.TaskResult("ok", 93, 123, None, "new-model", 1.1, 2.2),
                    partial_and_final,
                ),
            ),
            mock.patch.object(runner, "save_runs"),
            mock.patch.object(runner, "save_cost_events", side_effect=lambda rows: saved_rows.extend(rows)),
            mock.patch.object(runner, "now_iso_z", return_value="2026-09-03T12:00:00Z"),
        ):
            self.assertEqual(runner.main(), 0)

        self.assertEqual(len(saved_rows), 4)
        self.assertEqual([row["api_model"] for row in saved_rows], ["old-model", "new-model", "new-model", "new-model"])
        self.assertEqual([row["attempt"] for row in saved_rows], [1, 2, 2, 2])
        self.assertEqual([row["canonical"] for row in saved_rows], [False, True, True, True])
        self.assertAlmostEqual(saved_rows[0]["estimated_cost_usd"], 10 / 1_000_000 * 0.5 + 20 / 1_000_000 * 1.5)

    def test_replay_replaces_prior_meter_event_group_and_stale_fallback(self) -> None:
        model = {
            "provider_id": "openai",
            "model_id": "gpt-pinned",
            "tier": "workhorse",
            "input_price": 1.0,
            "output_price": 2.0,
        }
        fake_eq = {
            "pricing_snapshot_date": "2026-08-31",
            "tasks": [{"task_id": "E", "output_cap": None}],
            "selected_models_by_mode": {"two": [model], "three": [model]},
        }
        existing = [
            {
                "event_id": "2026-09-03:meter:openai:workhorse:E:1:generation:old-1:1",
                "date": "2026-09-03",
                "run_at": "2026-09-03T11:59:00Z",
                "source": "meter",
                "provider_id": "openai",
                "tier": "workhorse",
                "task_id": "E",
                "turn": 1,
                "request_kind": "generation",
                "api_model": "old-model",
                "input_tokens": 10,
                "output_tokens": 20,
                "input_price_per_1m": 0.5,
                "output_price_per_1m": 1.5,
                "input_cost_usd": 0.0,
                "output_cost_usd": 0.0,
                "estimated_cost_usd": 0.0,
                "pricing_snapshot_date": "2026-08-31",
                "corpus_version": "3.0.0",
                "chat_corpus_version": "2.0.0",
                "run_id": "2026-09-03:two:1:old",
                "replicate": 1,
                "attempt": 1,
                "canonical": False,
                "complete": True,
            },
            {
                "event_id": "2026-09-03:meter:openai:workhorse:E:2:generation:old-2:1",
                "date": "2026-09-03",
                "run_at": "2026-09-03T11:59:01Z",
                "source": "meter",
                "provider_id": "openai",
                "tier": "workhorse",
                "task_id": "E",
                "turn": 2,
                "request_kind": "generation",
                "api_model": "old-model",
                "input_tokens": 30,
                "output_tokens": 40,
                "input_price_per_1m": 0.5,
                "output_price_per_1m": 1.5,
                "input_cost_usd": 0.0,
                "output_cost_usd": 0.0,
                "estimated_cost_usd": 0.0,
                "pricing_snapshot_date": "2026-08-31",
                "corpus_version": "3.0.0",
                "chat_corpus_version": "2.0.0",
                "run_id": "2026-09-03:two:1:old",
                "replicate": 1,
                "attempt": 1,
                "canonical": False,
                "complete": True,
            },
            {
                "event_id": "keep-non-meter",
                "date": "2026-09-03",
                "run_at": "2026-09-03T11:00:00Z",
                "source": "ledger",
                "provider_id": "openai",
                "tier": "workhorse",
                "task_id": "E",
                "turn": 1,
                "request_kind": "count_endpoint",
                "api_model": "gpt-live",
                "input_tokens": 1,
                "output_tokens": 0,
                "input_price_per_1m": 0.0,
                "output_price_per_1m": 0.0,
                "input_cost_usd": 0.0,
                "output_cost_usd": 0.0,
                "estimated_cost_usd": 0.0,
                "pricing_snapshot_date": "2026-08-31",
                "corpus_version": "3.0.0",
                "chat_corpus_version": None,
                "run_id": "keep",
                "replicate": 1,
                "attempt": 1,
                "canonical": True,
                "complete": True,
            },
        ]
        turns = [
            runner.TurnResult(
                1,
                runner.Usage(11, 21, False, None, "new-1"),
                [{"role": "user", "content": runner.E_USER_PROMPTS[0]}],
                1.1,
                2.2,
                "new-model",
            ),
            runner.TurnResult(
                2,
                runner.Usage(31, 41, False, None, "new-2"),
                [
                    {"role": "user", "content": runner.E_USER_PROMPTS[0]},
                    {"role": "assistant", "content": "new-1"},
                    {"role": "user", "content": runner.E_USER_PROMPTS[1]},
                ],
                1.1,
                2.2,
                "new-model",
            ),
            runner.TurnResult(
                3,
                runner.Usage(51, 61, False, None, "new-3"),
                [
                    {"role": "user", "content": runner.E_USER_PROMPTS[0]},
                    {"role": "assistant", "content": "new-1"},
                    {"role": "user", "content": runner.E_USER_PROMPTS[1]},
                    {"role": "assistant", "content": "new-2"},
                    {"role": "user", "content": runner.E_USER_PROMPTS[2]},
                ],
                1.1,
                2.2,
                "new-model",
            ),
        ]
        saved_rows: list[dict] = []
        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    "run_equivalence_tasks.py",
                    "--mode",
                    "two",
                    "--date",
                    "2026-09-03",
                    "--workhorse-replicates",
                    "1",
                ],
            ),
            mock.patch.object(runner, "METER_TASK_IDS", ("E",)),
            mock.patch.object(runner, "load_equivalence", return_value=fake_eq),
            mock.patch.object(runner, "load_existing_runs", return_value=[]),
            mock.patch.object(runner, "load_cost_events", return_value=existing),
            mock.patch.object(
                runner,
                "run_provider_conversation",
                return_value=(
                    runner.TaskResult("ok", 93, 123, None, "new-model", 1.1, 2.2),
                    turns,
                ),
            ),
            mock.patch.object(runner, "save_runs"),
            mock.patch.object(runner, "save_cost_events", side_effect=lambda rows: saved_rows.extend(rows)),
            mock.patch.object(runner, "now_iso_z", return_value="2026-09-03T12:00:00Z"),
        ):
            self.assertEqual(runner.main(), 0)

        meter_rows = [row for row in saved_rows if row.get("source") == "meter"]
        self.assertEqual(len(meter_rows), 3)
        self.assertTrue(all(row["api_model"] == "new-model" for row in meter_rows))
        self.assertTrue(any(row["event_id"] == "keep-non-meter" for row in saved_rows))
        total = sum(row["estimated_cost_usd"] for row in meter_rows if row.get("estimated_cost_usd") is not None)
        self.assertAlmostEqual(
            total,
            (11 / 1_000_000 * 1.1 + 21 / 1_000_000 * 2.2)
            + (31 / 1_000_000 * 1.1 + 41 / 1_000_000 * 2.2)
            + (51 / 1_000_000 * 1.1 + 61 / 1_000_000 * 2.2),
        )


class ErrorRedactionTest(unittest.TestCase):
    def test_redacts_bearer_and_common_token_patterns(self) -> None:
        response = _response(
            401,
            text=(
                "Authorization: Bearer sk-live-secret token=abc123 key=xyz "
                "api_key=foo x-api-key: bar sk-rawvalue"
            ),
        )
        exc = requests.HTTPError("401 Client Error", response=response)
        message = runner._http_error_detail(exc)
        self.assertNotIn("sk-live-secret", message)
        self.assertNotIn("abc123", message)
        self.assertNotIn("xyz", message)
        self.assertNotIn("foo", message)
        self.assertNotIn("bar", message)
        self.assertNotIn("sk-rawvalue", message)
        self.assertIn("Bearer [REDACTED]", message)

    def test_redacts_json_and_key_formats(self) -> None:
        response = _response(
            400,
            text=(
                '{"api_key":"secret-1","token":"secret-2","jwt":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def",'
                '"google":"AIzaSyD123456789012345678901234567890123","aws":"AKIA1234567890ABCD12"}'
            ),
        )
        exc = requests.HTTPError("boom", response=response)
        redacted = runner._http_error_detail(exc)
        self.assertNotIn("secret-1", redacted)
        self.assertNotIn("secret-2", redacted)
        self.assertNotIn("AIzaSyD123456789012345678901234567890123", redacted)
        self.assertNotIn("AKIA1234567890ABCD12", redacted)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def", redacted)

    def test_generic_exception_text_is_redacted(self) -> None:
        entry = {
            "provider_id": "openai",
            "model_id": "openai-pinned",
            "tier": "workhorse",
            "input_price": 1.0,
            "output_price": 2.0,
        }
        with (
            mock.patch.object(runner, "env_for_provider", return_value="k"),
            mock.patch.object(runner, "candidate_plan", return_value=[("gpt-live", 1.0, 2.0)]),
            mock.patch.object(
                runner,
                "_run_one",
                side_effect=RuntimeError(
                    'boom {"api_key":"secret","token":"abc","jwt":"eyJhbGciOiJIUzI1NiJ9.a.b"}'
                ),
            ),
        ):
            result = runner.run_provider_task(entry, "A", None, False)
        self.assertEqual(result.status, "error")
        self.assertNotIn("secret", result.error or "")
        self.assertNotIn("abc", result.error or "")

    def test_generic_conversation_exception_text_is_redacted(self) -> None:
        entry = {
            "provider_id": "openai",
            "model_id": "openai-pinned",
            "tier": "workhorse",
            "input_price": 1.0,
            "output_price": 2.0,
        }
        with (
            mock.patch.object(runner, "env_for_provider", return_value="k"),
            mock.patch.object(runner, "candidate_plan", return_value=[("gpt-live", 1.0, 2.0)]),
            mock.patch.object(
                runner,
                "_run_messages",
                side_effect=RuntimeError("Bearer sk-secret AIzaSyD123456789012345678901234567890123"),
            ),
        ):
            result, turns = runner.run_provider_conversation(entry, None, False)
        self.assertEqual(result.status, "error")
        self.assertEqual(turns, [])
        self.assertNotIn("sk-secret", result.error or "")


if __name__ == "__main__":
    unittest.main()
