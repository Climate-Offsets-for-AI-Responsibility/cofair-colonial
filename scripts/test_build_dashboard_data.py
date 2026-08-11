#!/usr/bin/env python3
"""Unit tests for dashboard artifact normalization."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_dashboard_data import normalize_snapshot


class NormalizeSnapshotTest(unittest.TestCase):
    def test_v2_passes_modality_and_context_window(self) -> None:
        snap = {
            "meta": {"schema_version": "2.0"},
            "pricing": [
                {
                    "pricing_id": "google-gemini-2.5-flash-standard-200k-audio-gemini25",
                    "provider_id": "google",
                    "model_id": "gemini-2.5-flash",
                    "display_name": "Gemini 2.5 Flash",
                    "service_tier": "standard",
                    "context_window": ">200k",
                    "modality": "audio",
                    "category": "gemini_2_5",
                    "input_unit": "per_1M_tokens",
                    "input_price": 1.0,
                    "output_price": None,
                    "cache_read_price": None,
                    "currency": "USD",
                    "is_active": True,
                }
            ],
        }
        rows = normalize_snapshot(snap, "2026-08-10")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["modality"], "audio")
        self.assertEqual(rows[0]["context_window"], ">200k")


if __name__ == "__main__":
    unittest.main()
