from __future__ import annotations

import unittest
from datetime import date

from model_families import is_eligible_model, model_role, rank_ids_for_tier


class ModelRoleTest(unittest.TestCase):
    def test_provider_role_policies_cover_the_panel(self) -> None:
        cases = [
            ("anthropic", "claude-opus-5", "flagship"),
            ("anthropic", "claude-haiku-4.5", "workhorse"),
            ("openai", "chat-latest", "flagship"),
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
            ("qwen", "qwen-flash", "workhorse"),
        ]
        for provider_id, model_id, expected in cases:
            with self.subTest(provider_id=provider_id, model_id=model_id):
                self.assertEqual(model_role(provider_id, model_id), expected)

    def test_non_panel_families_are_not_misclassified(self) -> None:
        cases = [
            ("anthropic", "claude-sonnet-5"),
            ("openai", "gpt-5.6-codex"),
            ("google", "gemini-3.1-flash-lite"),
            ("google", "gemini-3-pro-image"),
            ("xai", "grok-code-fast"),
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
            ("openai", "flagship", ["gpt-5.6-sol", "chat-latest"], "chat-latest"),
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


if __name__ == "__main__":
    unittest.main()
