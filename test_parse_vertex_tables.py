#!/usr/bin/env python3
"""Unit tests for Vertex Gemini table parsing (no network)."""
from __future__ import annotations

import unittest

import scrape_pricing as m


SAMPLE_STANDARD = """
<html><body>
<h2>Gemini 3</h2>
<button>Standard</button><button>Priority</button><button>Flex/Batch</button>
<table>
  <tr>
    <th>Model</th><th>Type</th><th>Region</th>
    <th>Price (/1M tokens) &lt;= 200K input tokens</th>
    <th>Price (/1M tokens) &gt; 200K input tokens</th>
  </tr>
  <tr>
    <td>Gemini 3.5 Flash</td><td>Input (text, image, video, audio)</td><td>Global</td>
    <td>$1.50</td><td>$1.50</td>
  </tr>
  <tr>
    <td></td><td></td><td>Non-global *</td>
    <td>$1.65</td><td>$1.65</td>
  </tr>
  <tr>
    <td></td><td>Text output (response and reasoning)</td><td>Global</td>
    <td>$9.00</td><td>$9.00</td>
  </tr>
</table>
</body></html>
"""

SAMPLE_PRIORITY = """
<html><body>
<h2>Gemini 2.5</h2>
<table>
  <tr>
    <th>Model</th><th>Type</th>
    <th>Price (/1M tokens) &lt;= 200K input tokens with Priority</th>
    <th>Price (/1M tokens) &gt; 200K input tokens with Priority</th>
  </tr>
  <tr>
    <td>Gemini 2.5 Pro</td><td>Input (text, image, video, audio)</td>
    <td>$2.25</td><td>$4.50</td>
  </tr>
  <tr>
    <td></td><td>Text output (response and reasoning)</td>
    <td>$18.00</td><td>$27.00</td>
  </tr>
</table>
</body></html>
"""


class ParseVertexTablesTest(unittest.TestCase):
    def test_standard_table_skips_nonglobal_and_reads_type_column(self):
        rows = m.parse_vertex(SAMPLE_STANDARD)
        self.assertGreaterEqual(len(rows), 4)
        self.assertTrue(all(r["service_tier"] == "standard" for r in rows))
        self.assertFalse(any(r["price"] == 1.65 for r in rows))
        inputs = [r for r in rows if r["component"] == "input"]
        outputs = [r for r in rows if r["component"] == "output"]
        self.assertEqual(inputs[0]["price"], 1.5)
        self.assertEqual(inputs[0]["modality"], "multimodal")
        self.assertEqual(outputs[0]["price"], 9.0)
        self.assertEqual(outputs[0]["modality"], "text")

    def test_priority_inferred_from_headers_not_flex_tab(self):
        rows = m.parse_vertex(SAMPLE_PRIORITY)
        self.assertTrue(rows)
        self.assertTrue(all(r["service_tier"] == "priority" for r in rows))
        self.assertEqual(rows[0]["display_name"], "Gemini 2.5 Pro")


if __name__ == "__main__":
    unittest.main()
