#!/usr/bin/env python3
"""Unit tests for scrape sanity + fallback remediation."""
from __future__ import annotations

import unittest

import scrape_pricing as m


def mk_row(provider: str, idx: int) -> dict:
    return {
        "pricing_id": f"{provider}-model-{idx}",
        "model_id": f"model-{idx}",
        "display_name": f"Model {idx}",
        "provider_id": provider,
        "component": "input",
        "price": 1.0,
        "unit": "per_1M_tokens",
        "currency": "USD",
        "service_tier": "standard",
        "context_window": ">200k",
        "modality": "text",
        "category": "test",
        "billing_variant": None,
        "is_active": True,
        "input_price": 1.0,
        "output_price": None,
        "cached_input_price": None,
    }


class ScrapeResilienceTest(unittest.TestCase):
    def test_evaluate_sanity_flags_provider_drop(self):
        old_rows = (
            [mk_row("anthropic", i) for i in range(15)]
            + [mk_row("google", i) for i in range(60)]
            + [mk_row("openai", i) for i in range(40)]
        )
        current_rows = (
            [mk_row("anthropic", i) for i in range(15)]
            + [mk_row("google", i) for i in range(5)]
            + [mk_row("openai", i) for i in range(40)]
        )
        sanity = m.evaluate_sanity(current_rows, old_rows, "2.1.0")
        self.assertTrue(any("google" in issue for issue in sanity["issues"]))

    def test_remediation_uses_previous_provider_rows(self):
        old_rows = (
            [mk_row("anthropic", i) for i in range(15)]
            + [mk_row("google", i) for i in range(60)]
            + [mk_row("openai", i) for i in range(40)]
        )
        current_rows = (
            [mk_row("anthropic", i) for i in range(15)]
            + [mk_row("google", i) for i in range(5)]
            + [mk_row("openai", i) for i in range(40)]
        )
        merged, remediation = m.remediate_with_previous_rows(current_rows, old_rows, "2.1.0")
        self.assertTrue(remediation["applied"])
        self.assertIn("google", remediation["providers"])
        counts = m.provider_counts(merged)
        self.assertEqual(counts["google"], 60)


    def test_remediation_sort_without_unit_field(self):
        old_rows = [mk_row("google", i) for i in range(60)]
        current_rows = [mk_row("google", i) for i in range(5)]
        merged, remediation = m.remediate_with_previous_rows(current_rows, old_rows, "2.1.0")
        self.assertTrue(remediation["applied"])
        # Tier rows from mk_row include unit; strip to simulate aggregated tier shape.
        for row in merged:
            if row["provider_id"] == "google":
                row.pop("unit", None)
        merged.sort(key=m._tier_sort_key)
        self.assertEqual(len(merged), 60)


if __name__ == "__main__":
    unittest.main()
