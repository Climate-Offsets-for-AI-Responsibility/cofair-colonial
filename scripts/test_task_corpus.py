#!/usr/bin/env python3
"""Tests for the frozen token-equivalence / drift corpus."""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_long_natural import LONG_NATURAL_CONTEXT
from corpus_long_packet import LONG_CONTEXT_PACKET
from task_corpus import (
    CHAT_CORPUS_VERSION,
    CHAT_TRANSCRIPT,
    CORPUS_VERSION,
    DEGENERATE_TASK_IDS,
    E_USER_PROMPTS,
    GENERATING_TASK_IDS,
    LEDGER_TASK_IDS,
    METER_TASK_IDS,
    MIN_LEXICAL_VARIETY,
    TASK_IDS,
    TASK_PACKS,
    TASK_PROMPTS,
    TASK_SPECS,
    _version_tuple,
    is_degenerate,
    lexical_variety,
    transcript_prefix,
)


class CanonicalCorpusTest(unittest.TestCase):
    def test_the_full_suite_is_canonical_a_through_f(self) -> None:
        self.assertEqual(TASK_IDS, ("A", "B", "C", "D", "E", "F"))
        self.assertEqual(GENERATING_TASK_IDS, TASK_IDS)
        self.assertEqual(LEDGER_TASK_IDS, ("A", "B", "C", "D", "F"))
        self.assertEqual(TASK_PACKS["suiteLong"], list(TASK_IDS))

    def test_task_e_is_three_frozen_relational_prompts(self) -> None:
        self.assertEqual(len(E_USER_PROMPTS), 3)
        self.assertIn("flagship model", E_USER_PROMPTS[0])
        self.assertIn("Challenge your recommendation", E_USER_PROMPTS[1])
        self.assertIn("three-step policy", E_USER_PROMPTS[2])
        self.assertIn("E", TASK_PROMPTS)
        self.assertEqual(TASK_SPECS["E"]["label"], "Chat conversation")

    def test_the_daily_ledger_excludes_conversation_task_e(self) -> None:
        from run_tokenizer_ledger import TASK_SETS

        self.assertEqual(TASK_SETS["all"], list(LEDGER_TASK_IDS))


class LongContextPacketTest(unittest.TestCase):
    """Task D's document, replacing the repeated sentence it used to be.

    Same freeze discipline as task F: the bytes are the measurement, so an edit
    has to fail here rather than show up as a step in the trend a day later.
    """

    SHA256 = "8afad93c4ec20a76ec1ee477b42cabb94ce1e576e47c89f5d93050beaa145b1e"
    CHARS = 25047

    def test_document_is_byte_frozen(self) -> None:
        digest = hashlib.sha256(LONG_CONTEXT_PACKET.encode()).hexdigest()

        self.assertEqual(len(LONG_CONTEXT_PACKET), self.CHARS)
        self.assertEqual(
            digest,
            self.SHA256,
            "Task D's context changed. That is a corpus edit: bump CORPUS_VERSION "
            "and update this hash on purpose, or revert the text.",
        )

    def test_it_is_still_the_largest_request_in_the_suite(self) -> None:
        """D's other job. If it stopped being the longest task, the character
        span the fit depends on would collapse to F's 61x and the long-context
        surcharge probe would have nothing to probe with."""
        others = [len(TASK_PROMPTS[t]) for t in METER_TASK_IDS if t != "D"]
        self.assertGreater(len(TASK_PROMPTS["D"]), max(others))

    def test_it_is_no_longer_degenerate(self) -> None:
        self.assertGreater(lexical_variety("D"), MIN_LEXICAL_VARIETY)
        self.assertNotIn("D", DEGENERATE_TASK_IDS)
        self.assertEqual(sorted(DEGENERATE_TASK_IDS), [])

    def test_no_long_passage_repeats(self) -> None:
        """The failure mode being replaced. A packet assembled from boilerplate
        section headers would pass a whole-document variety check while still
        handing the tokenizer the same merges over and over."""
        for start in range(0, len(LONG_CONTEXT_PACKET) - 60, 37):
            window = LONG_CONTEXT_PACKET[start : start + 60]
            self.assertEqual(LONG_CONTEXT_PACKET.count(window), 1, msg=window[:40])

    def test_the_needle_is_stated_once_and_asked_for(self) -> None:
        """D is labelled a needle task and never was one. The answer has to be
        in the context exactly once, or the retrieval reading is meaningless."""
        self.assertEqual(LONG_CONTEXT_PACKET.count("192,000"), 1)
        self.assertEqual(LONG_CONTEXT_PACKET.count("NG-4471-B"), 1)
        self.assertIn("NG-4471-B", TASK_PROMPTS["D"])
        self.assertNotIn("192,000", TASK_PROMPTS["D"].split("Question:")[-1])

    def test_it_carries_the_features_vocabularies_diverge_on(self) -> None:
        text = LONG_CONTEXT_PACKET
        self.assertRegex(text, r"\$\d")
        self.assertRegex(text, r"\d%|\d percent")
        self.assertRegex(text, r"\d\.\d")
        self.assertRegex(text, r"\d{2}:\d{2}")
        self.assertIn("def ", text)
        self.assertRegex(text, r"[a-z]+-[a-z]+")


class DegeneracyIsVersionedTest(unittest.TestCase):
    """Task D's id did not change when its prompt did.

    Keying degeneracy on the task id alone was correct for exactly as long as D
    meant one thing. The moment its prompt was replaced, that key would have
    readmitted eleven days of filler-derived counts to the historical fit —
    silently reintroducing the artifact D77 was raised to remove, and reporting
    a content rate for days on which no natural long text was ever collected.
    """

    def test_old_task_d_rows_are_still_degenerate(self) -> None:
        self.assertTrue(is_degenerate("D", "1.0.0"))
        self.assertTrue(is_degenerate("D", "1.1.0"))

    def test_new_task_d_rows_are_not(self) -> None:
        self.assertFalse(is_degenerate("D", "2.0.0"))
        self.assertFalse(is_degenerate("D", "2.1.0"))
        self.assertFalse(is_degenerate("D", CORPUS_VERSION))

    def test_an_unversioned_row_is_treated_as_old(self) -> None:
        """Safe direction: a row with no version predates versioning, so it
        predates the replacement."""
        self.assertTrue(is_degenerate("D", None))
        self.assertTrue(is_degenerate("D", ""))
        self.assertTrue(is_degenerate("D", "not-a-version"))

    def test_other_tasks_are_unaffected_by_version(self) -> None:
        for task_id in ("A", "B", "C", "F"):
            self.assertFalse(is_degenerate(task_id, "1.0.0"), msg=task_id)
            self.assertFalse(is_degenerate(task_id, CORPUS_VERSION), msg=task_id)

    def test_the_replacement_was_declared_as_breaking(self) -> None:
        """A prompt change that is not a major bump would let the two halves of
        the D series be read as one."""
        self.assertGreaterEqual(_version_tuple(CORPUS_VERSION), (2, 0, 0))


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

    def test_it_is_varied_enough_to_price_a_tokenizer(self) -> None:
        """The whole reason it exists: variety at length.

        Written when task D was still `"Policy review context sentence. " * 800`
        and F was the only long task a fit could rest on. D has since been
        replaced and clears the floor too, so this no longer asserts anything
        about D — but F still has to clear it on its own, or the corpus is one
        prompt edit away from having no usable long text again.
        """
        self.assertGreater(lexical_variety("F"), MIN_LEXICAL_VARIETY)
        self.assertNotIn("F", DEGENERATE_TASK_IDS)

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

        self.assertEqual(TASK_SETS["all"], list(LEDGER_TASK_IDS))

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
