#!/usr/bin/env python3
"""Daily snapshots must land even when list prices did not move.

The /pricing chart is built only from dated files in pricing_history/. Skipping
an unchanged day leaves a hole, so the live index stops at the last *change*
instead of the last scrape — which is what froze cofair.org/pricing on 2026-09-04.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot_if_changed import snapshot_pricing


def _doc(input_price: float = 1.0) -> dict:
    return {
        "meta": {"schema_version": "2.1.0", "last_run_datetime": "2026-09-08T12:00:00Z"},
        "providers": [{"id": "openai"}],
        "pricing": [{"pricing_id": "openai-gpt", "input_price": input_price}],
    }


class SnapshotPricingTest(unittest.TestCase):
    def test_writes_a_new_day_even_when_prices_match_the_previous_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hist = Path(tmp) / "history"
            hist.mkdir()
            src = Path(tmp) / "pricing.json"
            payload = _doc()
            (hist / "2026-09-04.json").write_text(json.dumps(payload))
            src.write_text(json.dumps(payload))

            message = snapshot_pricing(src, hist, "2026-09-08")

            dest = hist / "2026-09-08.json"
            self.assertTrue(dest.exists(), message)
            self.assertEqual(json.loads(dest.read_text())["pricing"], payload["pricing"])
            self.assertIn("2026-09-08.json", message)

    def test_same_day_rerun_is_a_noop_when_prices_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hist = Path(tmp) / "history"
            hist.mkdir()
            src = Path(tmp) / "pricing.json"
            payload = _doc()
            dest = hist / "2026-09-08.json"
            dest.write_text(json.dumps(payload))
            src.write_text(json.dumps(payload))
            before = dest.stat().st_mtime_ns

            message = snapshot_pricing(src, hist, "2026-09-08")

            self.assertEqual(json.loads(dest.read_text())["pricing"], payload["pricing"])
            self.assertEqual(dest.stat().st_mtime_ns, before)
            self.assertIn("skipping", message)

    def test_same_day_rerun_overwrites_when_prices_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hist = Path(tmp) / "history"
            hist.mkdir()
            src = Path(tmp) / "pricing.json"
            dest = hist / "2026-09-08.json"
            dest.write_text(json.dumps(_doc(1.0)))
            src.write_text(json.dumps(_doc(2.0)))

            message = snapshot_pricing(src, hist, "2026-09-08")

            self.assertEqual(json.loads(dest.read_text())["pricing"][0]["input_price"], 2.0)
            self.assertIn("wrote", message.lower())


if __name__ == "__main__":
    unittest.main()
