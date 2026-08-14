#!/usr/bin/env python3
"""Tests for the frozen token-equivalence / drift corpus."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from task_corpus import (
    CHAT_CORPUS_VERSION,
    CHAT_TRANSCRIPT,
    TASK_PROMPTS,
    transcript_prefix,
)


class ChatTranscriptTest(unittest.TestCase):
    def test_has_ten_user_and_ten_assistant_turns(self) -> None:
        self.assertEqual(len(CHAT_TRANSCRIPT), 20)
        roles = [turn["role"] for turn in CHAT_TRANSCRIPT]
        self.assertEqual(roles.count("user"), 10)
        self.assertEqual(roles.count("assistant"), 10)
        # Alternating user → assistant.
        for i, role in enumerate(roles):
            self.assertEqual(role, "user" if i % 2 == 0 else "assistant")

    def test_word_budgets_are_locked(self) -> None:
        for i, turn in enumerate(CHAT_TRANSCRIPT):
            words = len(turn["text"].split())
            if turn["role"] == "user":
                self.assertGreaterEqual(words, 12, msg=f"user turn {i}")
                self.assertLessEqual(words, 18, msg=f"user turn {i}")
            else:
                self.assertGreaterEqual(words, 45, msg=f"assistant turn {i}")
                self.assertLessEqual(words, 55, msg=f"assistant turn {i}")

    def test_prefix_includes_complete_turns_only(self) -> None:
        prefix = transcript_prefix(3)
        self.assertEqual(len(prefix), 6)
        self.assertEqual(prefix[-1]["role"], "assistant")
        self.assertEqual(transcript_prefix(0), [])
        self.assertEqual(len(transcript_prefix(10)), 20)

    def test_chat_corpus_is_versioned(self) -> None:
        self.assertTrue(CHAT_CORPUS_VERSION)
        # A–D prompts remain present for the meter + ledger.
        self.assertIn("A", TASK_PROMPTS)


if __name__ == "__main__":
    unittest.main()
