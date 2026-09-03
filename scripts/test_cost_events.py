#!/usr/bin/env python3
"""Tests for deterministic request-level cost events."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cost_events import (  # noqa: E402
    CostEventInput,
    build_cost_event,
    cost_event_id,
    load_cost_events,
    merge_cost_events,
    save_cost_events,
)

FORBIDDEN_EVENT_KEYS = frozenset(
    {
        "error",
        "api_key",
        "secret",
        "credential",
        "password",
        "token",
    }
)


def _generation_kwargs(**overrides) -> dict:
    base = {
        "date": "2026-09-03",
        "run_at": "2026-09-03T12:00:00Z",
        "source": "meter",
        "provider_id": "openai",
        "tier": "flagship",
        "task_id": "A",
        "turn": None,
        "request_kind": "generation",
        "api_model": "chat-latest",
        "input_tokens": 34,
        "output_tokens": 163,
        "input_price_per_1m": 1.25,
        "output_price_per_1m": 10.0,
        "pricing_snapshot_date": "2026-09-03",
        "corpus_version": "3.0.0",
        "chat_corpus_version": "2.0.0",
        "run_id": "2026-09-03:two:1",
    }
    base.update(overrides)
    return base


class CostEventIdTest(unittest.TestCase):
    def test_identity_is_deterministic(self) -> None:
        kwargs = {
            "date": "2026-09-03",
            "source": "meter",
            "provider_id": "openai",
            "tier": "flagship",
            "task_id": "A",
            "turn": None,
            "request_kind": "generation",
            "run_id": "2026-09-03:two:1",
        }
        self.assertEqual(cost_event_id(**kwargs), cost_event_id(**kwargs))
        self.assertEqual(
            cost_event_id(**kwargs),
            "2026-09-03:meter:openai:flagship:A:0:generation:2026-09-03:two:1:1",
        )

    def test_turn_and_replicate_affect_identity(self) -> None:
        base = {
            "date": "2026-09-03",
            "source": "meter",
            "provider_id": "openai",
            "tier": "flagship",
            "task_id": "E",
            "request_kind": "generation",
            "run_id": "2026-09-03:two:1",
        }
        turn_one = cost_event_id(**base, turn=1)
        turn_two = cost_event_id(**base, turn=2)
        self.assertNotEqual(turn_one, turn_two)
        self.assertEqual(
            cost_event_id(**base, turn=1, replicate=2),
            "2026-09-03:meter:openai:flagship:E:1:generation:2026-09-03:two:1:2",
        )


class BuildCostEventTest(unittest.TestCase):
    def test_generation_cost_uses_unrounded_usage(self) -> None:
        event = build_cost_event(**_generation_kwargs())
        self.assertAlmostEqual(event["input_cost_usd"], 34 / 1_000_000 * 1.25)
        self.assertAlmostEqual(event["output_cost_usd"], 163 / 1_000_000 * 10.0)
        self.assertAlmostEqual(
            event["estimated_cost_usd"],
            event["input_cost_usd"] + event["output_cost_usd"],
        )
        self.assertTrue(event["complete"])

    def test_incomplete_when_prices_missing(self) -> None:
        event = build_cost_event(
            **_generation_kwargs(input_price_per_1m=None, output_price_per_1m=None)
        )
        self.assertFalse(event["complete"])
        self.assertIsNone(event["input_cost_usd"])
        self.assertIsNone(event["output_cost_usd"])
        self.assertIsNone(event["estimated_cost_usd"])

    def test_incomplete_when_tokens_missing(self) -> None:
        event = build_cost_event(**_generation_kwargs(input_tokens=None, output_tokens=None))
        self.assertFalse(event["complete"])
        self.assertIsNone(event["estimated_cost_usd"])

    def test_missing_pricing_snapshot_date_is_incomplete(self) -> None:
        event = build_cost_event(**_generation_kwargs(pricing_snapshot_date=None))
        self.assertFalse(event["complete"])
        self.assertIsNone(event["input_cost_usd"])
        self.assertIsNone(event["output_cost_usd"])
        self.assertIsNone(event["estimated_cost_usd"])

    def test_count_endpoint_is_zero_cost_and_complete(self) -> None:
        event = build_cost_event(
            **_generation_kwargs(
                source="ledger",
                request_kind="count_endpoint",
                input_tokens=120,
                output_tokens=0,
                billable=False,
            )
        )
        self.assertTrue(event["complete"])
        self.assertEqual(event["input_cost_usd"], 0.0)
        self.assertEqual(event["output_cost_usd"], 0.0)
        self.assertEqual(event["estimated_cost_usd"], 0.0)

    def test_nonbillable_unknown_usage_stays_incomplete(self) -> None:
        event = build_cost_event(
            **_generation_kwargs(
                source="ledger",
                request_kind="count_endpoint",
                input_tokens=None,
                output_tokens=None,
                billable=False,
            )
        )
        self.assertFalse(event["complete"])
        self.assertIsNone(event["input_cost_usd"])
        self.assertIsNone(event["output_cost_usd"])
        self.assertIsNone(event["estimated_cost_usd"])

    def test_event_has_no_secret_or_error_fields(self) -> None:
        event = build_cost_event(**_generation_kwargs())
        for key in event:
            self.assertNotIn(key.lower(), FORBIDDEN_EVENT_KEYS)
        with self.assertRaises(TypeError):
            build_cost_event(**_generation_kwargs(error="boom"))
        with self.assertRaises(TypeError):
            build_cost_event(**_generation_kwargs(api_key="sk-secret"))

    def test_cost_event_input_dataclass_builds_same_event(self) -> None:
        kwargs = _generation_kwargs()
        from_input = build_cost_event(CostEventInput(**kwargs))
        from_kwargs = build_cost_event(**kwargs)
        self.assertEqual(from_input, from_kwargs)

    def test_cost_event_input_is_immutable(self) -> None:
        row = CostEventInput(**_generation_kwargs())
        with self.assertRaises(AttributeError):
            row.task_id = "B"  # type: ignore[misc]


class MergeCostEventsTest(unittest.TestCase):
    def test_replay_replaces_same_logical_request(self) -> None:
        old_event = build_cost_event(**_generation_kwargs(output_tokens=100))
        new_event = build_cost_event(**_generation_kwargs(output_tokens=200))
        merged = merge_cost_events([old_event], [new_event])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["estimated_cost_usd"], new_event["estimated_cost_usd"])
        self.assertEqual(merged[0]["output_tokens"], 200)

    def test_merge_keeps_distinct_events(self) -> None:
        first = build_cost_event(**_generation_kwargs(task_id="A"))
        second = build_cost_event(**_generation_kwargs(task_id="B"))
        merged = merge_cost_events([first], [second])
        self.assertEqual(len(merged), 2)

    def test_merge_sorts_rows(self) -> None:
        later = build_cost_event(**_generation_kwargs(task_id="B"))
        earlier = build_cost_event(**_generation_kwargs(task_id="A"))
        merged = merge_cost_events([later], [earlier])
        self.assertEqual([row["task_id"] for row in merged], ["A", "B"])


class PersistCostEventsTest(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        row = build_cost_event(**_generation_kwargs())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cost_events.json"
            save_cost_events([row], path=path)
            payload = json.loads(path.read_text())
            self.assertEqual(payload["row_count"], 1)
            self.assertEqual(len(payload["rows"]), 1)
            self.assertIn("generated_at", payload)
            loaded = load_cost_events(path=path)
            self.assertEqual(loaded, [row])

    def test_save_rejects_forbidden_keys(self) -> None:
        row = build_cost_event(**_generation_kwargs())
        bad = dict(row)
        bad["error"] = "secret detail"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cost_events.json"
            with self.assertRaises(ValueError):
                save_cost_events([bad], path=path)


if __name__ == "__main__":
    unittest.main()
