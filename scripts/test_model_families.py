from __future__ import annotations

import unittest
from datetime import date

from model_families import (
    everyday_list_price,
    is_eligible_model,
    model_role,
    order_workhorses,
    rank_ids_for_tier,
)


class ModelRoleTest(unittest.TestCase):
    def test_provider_role_policies_cover_the_panel(self) -> None:
        cases = [
            ("anthropic", "claude-opus-5", "flagship"),
            ("anthropic", "claude-haiku-4.5", "workhorse"),
            ("openai", "gpt-6-astra", "flagship"),
            ("openai", "gpt-5.6-sol", "flagship"),
            ("openai", "chat-latest", "workhorse"),
            ("openai", "gpt-5.6-luna", "workhorse"),
            ("google", "gemini-3.1-pro", "flagship"),
            ("google", "gemini-3.7-flash", "workhorse"),
            ("xai", "grok-4.6", "flagship"),
            ("xai", "grok-build-0.1", "workhorse"),
            ("aws", "nova-2.0-pro", "flagship"),
            ("aws", "nova-2.0-lite", "workhorse"),
            ("deepseek", "deepseek-v4-pro", "flagship"),
            ("deepseek", "deepseek-flash", "workhorse"),
            ("qwen", "qwen3.7-max", "flagship"),
            ("qwen", "qwen3.7-plus", "workhorse"),
            ("qwen", "qwen-flash", "workhorse"),
            ("anthropic", "claude-sonnet-5", "workhorse"),
        ]
        for provider_id, model_id, expected in cases:
            with self.subTest(provider_id=provider_id, model_id=model_id):
                self.assertEqual(model_role(provider_id, model_id), expected)

    def test_non_panel_families_are_not_misclassified(self) -> None:
        cases = [
            ("openai", "gpt-5.6-codex"),
            ("google", "gemini-3.1-flash-lite"),
            ("google", "gemini-3-pro-image"),
            ("xai", "grok-code-fast"),
            ("xai", "grok-4.20-0309-non-reasoning"),
            ("google", "gemini-3.8-flash-cyber"),
            ("aws", "titan-text-express"),
            ("deepseek", "deepseek-v4-vision"),
            ("qwen", "qwen3-coder"),
        ]
        for provider_id, model_id in cases:
            with self.subTest(provider_id=provider_id, model_id=model_id):
                self.assertIsNone(model_role(provider_id, model_id))


class ModelEligibilityTest(unittest.TestCase):
    AS_OF = date(2026, 9, 15)

    def test_excludes_unstable_retired_and_future_models(self) -> None:
        excluded = [
            "gemini-3-flash-preview",
            "deepseek-v4.1-flash-expires-on-0910",
            "grok-5-beta",
            "claude-opus-6-experimental",
            "nova-pro-legacy",
            "gemini-3.8-flash-starting-january-1-2027",
            "qwen3.7-max-2026-06-08-context-caching-discount",
        ]
        for model_id in excluded:
            with self.subTest(model_id=model_id):
                self.assertFalse(
                    is_eligible_model("google", model_id, model_id, self.AS_OF)
                )

    def test_accepts_stable_current_model(self) -> None:
        self.assertTrue(
            is_eligible_model(
                "google", "gemini-3.7-flash", "Gemini 3.7 Flash", self.AS_OF
            )
        )


class ModelRankingTest(unittest.TestCase):
    AS_OF = date(2026, 9, 15)

    def test_newest_eligible_version_wins_for_each_provider(self) -> None:
        cases = [
            ("anthropic", "flagship", ["claude-opus-4.8", "claude-opus-5"], "claude-opus-5"),
            ("openai", "flagship", ["gpt-5.6-sol", "gpt-6-sol", "gpt-6-astra", "chat-latest"], "gpt-6-astra"),
            ("openai", "flagship", ["gpt-5.6-sol", "gpt-6-sol", "chat-latest"], "gpt-6-sol"),
            ("openai", "workhorse", ["gpt-6-luna", "chat-latest"], "chat-latest"),
            (
                "google",
                "workhorse",
                [
                    "gemini-2.0-flash",
                    "gemini-3.7-flash",
                    "gemini-3.8-flash-starting-january-1-2027",
                ],
                "gemini-3.7-flash",
            ),
            ("xai", "flagship", ["grok-4.5", "grok-4.6"], "grok-4.6"),
            (
                "xai",
                "flagship",
                [
                    "grok-4.6",
                    "grok-4.7",
                    "grok-4.20-0309-non-reasoning",
                    "grok-4.20-multi-agent-0309",
                ],
                "grok-4.7",
            ),
            (
                "google",
                "workhorse",
                ["gemini-3.8-flash-cyber", "gemini-3.8-flash", "gemini-3.7-flash"],
                "gemini-3.8-flash",
            ),
            ("aws", "flagship", ["nova-premier", "nova-2.0-pro"], "nova-2.0-pro"),
            (
                "deepseek",
                "workhorse",
                ["deepseek-v4-flash", "deepseek-flash"],
                "deepseek-flash",
            ),
            (
                "qwen",
                "flagship",
                [
                    "qwen3-max",
                    "qwen3.7-max",
                    "qwen3.7-max-2026-06-08-context-caching-discount",
                ],
                "qwen3.7-max",
            ),
        ]
        for provider_id, tier, candidates, expected in cases:
            with self.subTest(provider_id=provider_id, tier=tier):
                self.assertEqual(
                    rank_ids_for_tier(provider_id, tier, candidates, self.AS_OF)[0],
                    expected,
                )

    def test_other_roles_are_not_returned_as_fallbacks(self) -> None:
        self.assertEqual(
            rank_ids_for_tier(
                "google",
                "flagship",
                ["gemini-3.1-pro", "gemini-3.7-flash"],
                self.AS_OF,
            ),
            ["gemini-3.1-pro"],
        )


class EverydayPriceTest(unittest.TestCase):
    def test_short_context_rate_is_the_comparison_price(self) -> None:
        price = everyday_list_price(
            [
                {
                    "context_window": "long_context",
                    "latest_input": 50.0,
                    "latest_output": 2.0,
                },
                {
                    "context_window": "short_context",
                    "latest_input": 10.0,
                    "latest_output": 12.5,
                },
            ]
        )
        self.assertEqual(price, 22.5)

    def test_short_prompt_band_beats_the_long_prompt_surcharge(self) -> None:
        price = everyday_list_price(
            [
                {
                    "billing_variant": "gte-200k-prompt",
                    "latest_input": 4.0,
                    "latest_output": 12.0,
                },
                {
                    "billing_variant": "lt-200k-prompt",
                    "latest_input": 2.0,
                    "latest_output": 6.0,
                },
            ]
        )
        self.assertEqual(price, 8.0)


class WorkhorsePriceTest(unittest.TestCase):
    AS_OF = date(2026, 9, 28)

    def test_openai_workhorse_is_sol_when_chat_costs_more_than_astra(self) -> None:
        prices = {
            "gpt-6-astra": 22.5,
            "gpt-6-sol": 4.5,
            "gpt-6-luna": 0.225,
            "chat-latest": 35.0,
        }
        ordered = order_workhorses(
            "openai",
            list(prices),
            prices,
            "gpt-6-astra",
            self.AS_OF,
        )
        self.assertEqual(ordered[0], "gpt-6-sol")
        self.assertLess(ordered.index("gpt-6-luna"), ordered.index("chat-latest"))

    def test_sol_stays_flagship_when_astra_is_absent(self) -> None:
        ordered = order_workhorses(
            "openai",
            ["gpt-6-sol", "gpt-6-luna", "chat-latest"],
            {"gpt-6-sol": 4.5, "gpt-6-luna": 0.225, "chat-latest": 35.0},
            "gpt-6-sol",
            self.AS_OF,
        )
        self.assertEqual(ordered[0], "gpt-6-luna")

    def test_anthropic_workhorse_is_the_step_under_opus(self) -> None:
        ordered = order_workhorses(
            "anthropic",
            ["claude-opus-5.5", "claude-sonnet-4.6", "claude-sonnet-5", "claude-haiku-4.5"],
            {
                "claude-opus-5.5": 24.0,
                "claude-sonnet-4.6": 18.0,
                "claude-sonnet-5": 12.0,
                "claude-haiku-4.5": 6.0,
            },
            "claude-opus-5.5",
            self.AS_OF,
        )
        self.assertEqual(ordered[0], "claude-sonnet-5")

    def test_qwen_workhorse_is_plus_not_the_floor(self) -> None:
        ordered = order_workhorses(
            "qwen",
            ["qwen3.7-max", "qwen3.7-plus", "qwen-flash"],
            {"qwen3.7-max": 10.0, "qwen3.7-plus": 2.0, "qwen-flash": 0.45},
            "qwen3.7-max",
            self.AS_OF,
        )
        self.assertEqual(ordered[0], "qwen3.7-plus")

    def test_an_older_flagship_sku_does_not_become_the_workhorse(self) -> None:
        ordered = order_workhorses(
            "aws",
            ["nova-premier", "nova-2.0-pro", "nova-pro", "nova-2.0-lite", "nova-micro"],
            {
                "nova-premier": 15.0,
                "nova-2.0-pro": 12.375,
                "nova-pro": 4.0,
                "nova-2.0-lite": 3.08,
                "nova-micro": 0.175,
            },
            "nova-2.0-pro",
            self.AS_OF,
        )
        self.assertEqual(ordered[0], "nova-2.0-lite")
        self.assertNotIn("nova-pro", ordered)
        self.assertNotIn("nova-premier", ordered)


if __name__ == "__main__":
    unittest.main()
