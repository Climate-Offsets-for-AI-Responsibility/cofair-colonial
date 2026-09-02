#!/usr/bin/env python3
"""Tests for the frozen token-equivalence / drift corpus."""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_long_natural import LONG_NATURAL_CONTEXT
from task_corpus import (
    CHAT_CORPUS_VERSION,
    CHAT_TRANSCRIPT,
    DEGENERATE_TASK_IDS,
    METER_TASK_IDS,
    MIN_LEXICAL_VARIETY,
    TASK_PROMPTS,
    lexical_variety,
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


class LongNaturalCorpusTest(unittest.TestCase):
    """Task F's document is frozen, and frozen means pinned, not merely intended.

    Every series on it is comparable only while the bytes are identical, and prose
    is the kind of thing a future reader tidies without thinking of it as a data
    change. The hash makes that edit fail loudly here instead of quietly producing
    a step in the trend the day after.
    """

    SHA256 = "3b77024ef191875b48d6603b1c3a1ffa545f950fa64076aa5cea71d2f1d8c45e"
    CHARS = 9483

    def test_document_is_byte_frozen(self) -> None:
        digest = hashlib.sha256(LONG_NATURAL_CONTEXT.encode()).hexdigest()

        self.assertEqual(len(LONG_NATURAL_CONTEXT), self.CHARS)
        self.assertEqual(
            digest,
            self.SHA256,
            "Task F's context changed. That is a corpus edit: bump CORPUS_VERSION "
            "and update this hash on purpose, or revert the text.",
        )

    def test_it_is_the_opposite_of_task_d(self) -> None:
        """The whole reason it exists: variety at length.

        Task D is long and repetitive, so it cannot price a tokenizer. F has to be
        long *and* varied, or it is just a second task D.
        """
        self.assertGreater(lexical_variety("F"), 20 * lexical_variety("D"))
        self.assertGreater(lexical_variety("F"), MIN_LEXICAL_VARIETY)
        self.assertNotIn("F", DEGENERATE_TASK_IDS)
        self.assertIn("D", DEGENERATE_TASK_IDS)

    def test_no_long_passage_repeats(self) -> None:
        """A subtler way to fail: assembling filler from a handful of sentences
        would pass a variety check on the whole document while still handing the
        tokenizer the same merges over and over."""
        for start in range(0, len(LONG_NATURAL_CONTEXT) - 60, 37):
            window = LONG_NATURAL_CONTEXT[start : start + 60]
            self.assertEqual(LONG_NATURAL_CONTEXT.count(window), 1, msg=window[:40])

    def test_it_spans_far_enough_to_anchor_the_fit(self) -> None:
        self.assertGreaterEqual(len(TASK_PROMPTS["F"]) / len(TASK_PROMPTS["A"]), 10.0)

    def test_the_daily_ledger_collects_every_corpus_task(self) -> None:
        """The gap that would have made all of this inert.

        The fit reads ledger rows, and the ledger workflow used to name its tasks
        literally (`--tasks ABCD`). Task F could therefore be added to the corpus,
        published in the task table, costed in the budget — and never collected,
        leaving content density withheld forever with nothing failing to say why.
        Asserted against the workflow file because that is where the drift lives.
        """
        from run_tokenizer_ledger import TASK_SETS

        self.assertEqual(TASK_SETS["all"], list(METER_TASK_IDS))

        workflow = (
            Path(__file__).resolve().parent.parent
            / ".github"
            / "workflows"
            / "daily-tokenizer-ledger.yml"
        ).read_text()
        self.assertIn("--tasks all", workflow)
        self.assertNotRegex(
            workflow,
            r"--tasks\s+(ABC|ABCD|D)\b",
            "The daily ledger names its tasks literally, so a task added to the "
            "corpus will not be collected. Use --tasks all.",
        )

    def test_it_carries_the_features_vocabularies_diverge_on(self) -> None:
        text = LONG_NATURAL_CONTEXT
        self.assertRegex(text, r"\d")
        self.assertRegex(text, r"\$\d")
        self.assertRegex(text, r"\d%")
        self.assertRegex(text, r"\d\.\d")
        self.assertIn("def ", text)  # an identifier-heavy code fragment
        self.assertRegex(text, r"[a-z]+-[a-z]+")  # hyphenation


if __name__ == "__main__":
    unittest.main()
