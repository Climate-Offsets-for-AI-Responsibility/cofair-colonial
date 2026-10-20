"""The OpenAI history repair fills holes only when the card agrees with itself."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from repair_openai_history import repair_snapshots


def row(pricing_id, output, day_price_input=0.4, cached=0.04):
    model = pricing_id.split("-standard-")[0].removeprefix("openai-")
    return {
        "pricing_id": pricing_id,
        "model_id": model,
        "display_name": model,
        "provider_id": "openai",
        "service_tier": "standard",
        "context_window": "long_context" if "longcontext" in pricing_id else "short_context",
        "modality": None,
        "category": "flagship",
        "currency": "USD",
        "is_active": True,
        "input_price": day_price_input,
        "input_unit": "per_1M_tokens",
        "cached_input_price": cached,
        "cached_input_unit": "per_1M_tokens",
        "output_price": output,
        "output_unit": "per_1M_tokens",
    }


LUNA = "openai-gpt-5.6-luna-standard-longcontext-flagship"
ASTRA = "openai-gpt-6-astra-standard-longcontext-flagship"
LATE = "openai-gpt-4o-standard-flagship"


class RepairOpenAIHistoryTest(unittest.TestCase):
    def test_replaces_the_shifted_column_and_restores_the_missing_days(self) -> None:
        wrong = row(LUNA, 0.04, day_price_input=1.2, cached=0.4)
        right = row(LUNA, 1.8)
        snapshots = {
            "2026-09-22": [wrong],
            "2026-09-23": [],
        }
        reparsed = {"2026-09-22": [right]}
        trusted = {"2026-10-17": [right]}

        rewritten, changes = repair_snapshots(snapshots, reparsed, trusted)

        self.assertEqual(rewritten["2026-09-22"][0]["output_price"], 1.8)
        self.assertEqual(rewritten["2026-09-22"][0]["input_price"], 0.4)
        restored = rewritten["2026-09-23"]
        self.assertEqual(restored[0]["pricing_id"], LUNA)
        self.assertEqual(restored[0]["output_price"], 1.8)
        self.assertIn(("insert", "2026-09-23", LUNA, (0.4, 0.04, 1.8)), changes)

    def test_does_not_backdate_a_model_that_was_not_on_the_card_yet(self) -> None:
        late = row(LUNA, 1.8)
        snapshots = {"2026-09-03": [], "2026-09-23": [row(LUNA, 0.04)]}
        reparsed = {"2026-09-23": [late]}
        trusted = {"2026-10-17": [late]}

        rewritten, _changes = repair_snapshots(snapshots, reparsed, trusted)

        self.assertNotIn("2026-09-03", rewritten)

    def test_does_not_project_an_oct_17_model_backward(self) -> None:
        born = row(LATE, 10.0, day_price_input=2.5, cached=1.25)
        snapshots = {"2026-09-15": [row(ASTRA, 2.0, day_price_input=50.0, cached=20.0)]}
        reparsed = {"2026-09-15": [row(ASTRA, 75.0, day_price_input=20.0, cached=2.0)]}
        trusted = {"2026-10-17": [born, row(ASTRA, 75.0, day_price_input=20.0, cached=2.0)]}

        rewritten, _changes = repair_snapshots(snapshots, reparsed, trusted)

        ids = [item["pricing_id"] for item in rewritten["2026-09-15"]]
        self.assertNotIn(LATE, ids)
        self.assertEqual(rewritten["2026-09-15"][0]["output_price"], 75.0)

    def test_a_real_price_change_is_not_averaged_across_the_hole(self) -> None:
        snapshots = {
            "2026-09-22": [row(LUNA, 0.04)],
            "2026-09-23": [],
        }
        reparsed = {"2026-09-22": [row(LUNA, 1.0)]}
        trusted = {"2026-10-17": [row(LUNA, 1.8)]}

        rewritten, changes = repair_snapshots(snapshots, reparsed, trusted)

        self.assertEqual(rewritten["2026-09-22"][0]["output_price"], 1.0)
        self.assertNotIn("2026-09-23", rewritten)
        self.assertFalse(any(kind == "insert" for kind, *_rest in changes))

    def test_a_full_card_reparse_does_not_add_models_the_scrape_never_stored(self) -> None:
        late = row(LATE, 10.0, day_price_input=2.5, cached=1.25)
        right = row(ASTRA, 75.0, day_price_input=20.0, cached=2.0)
        snapshots = {"2026-09-15": [row(ASTRA, 2.0, day_price_input=50.0, cached=20.0)]}
        rewritten, _changes = repair_snapshots(
            snapshots,
            {"2026-09-15": [right, late]},
            {"2026-10-17": [right, late]},
        )
        ids = [item["pricing_id"] for item in rewritten["2026-09-15"]]
        self.assertEqual(ids, [ASTRA])
        self.assertNotIn(LATE, ids)

    def test_a_second_pass_changes_nothing(self) -> None:
        right = row(LUNA, 1.8)
        snapshots = {"2026-09-22": [right], "2026-09-23": [right]}
        rewritten, changes = repair_snapshots(snapshots, {"2026-09-22": [right]}, {"2026-10-17": [right]})
        self.assertEqual(rewritten, {})
        self.assertEqual(changes, [])


if __name__ == "__main__":
    unittest.main()
