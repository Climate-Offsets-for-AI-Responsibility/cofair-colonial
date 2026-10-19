#!/usr/bin/env python3
"""Unit tests for additional pricing sources in scrape_pricing.py."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import date

from scrape_pricing import (
    is_amazon_owned_model,
    is_deepseek_owned_model,
    is_microsoft_owned_model,
    parse_amazon_bedrock,
    parse_claude,
    parse_deepseek,
    parse_microsoft,
    parse_openai,
    parse_qwen,
    parse_vertex,
    parse_xai,
)


def _amazon_price_dim(usd: str) -> dict:
    return {
        "offer": {
            "priceDimensions": {
                "d1": {
                    "unit": "1K tokens",
                    "pricePerUnit": {"USD": usd},
                    "description": f"${usd} per 1K tokens in US East (N. Virginia)",
                }
            }
        }
    }


class IsAmazonOwnedModelTest(unittest.TestCase):
    def test_accepts_nova_and_titan_text(self) -> None:
        self.assertTrue(is_amazon_owned_model({"model": "Nova Pro"}))
        self.assertTrue(is_amazon_owned_model({"usagetype": "USE1-TitanTextG1-Lite-input-tokens"}))

    def test_rejects_third_party_models(self) -> None:
        self.assertFalse(is_amazon_owned_model({"model": "Claude Opus 4.6"}))
        self.assertFalse(is_amazon_owned_model({"servicename": "Cohere Command R (Amazon Bedrock Edition)"}))
        self.assertFalse(is_amazon_owned_model({"usagetype": "USE1-MetaLlama2-input-tokens"}))


class ParseAmazonBedrockTest(unittest.TestCase):
    def test_keeps_only_amazon_standard_token_rows(self) -> None:
        payload = {
            "products": {
                "SKU_NOVA_IN": {
                    "attributes": {
                        "model": "Nova Pro",
                        "inferenceType": "Input tokens",
                        "feature": "On-demand Inference",
                        "usagetype": "USE1-NovaPro-input-tokens",
                    }
                },
                "SKU_NOVA_OUT": {
                    "attributes": {
                        "model": "Nova Pro",
                        "inferenceType": "Output tokens",
                        "feature": "On-demand Inference",
                        "usagetype": "USE1-NovaPro-output-tokens",
                    }
                },
                "SKU_NOVA_CACHE_READ": {
                    "attributes": {
                        "model": "Nova Pro",
                        "inferenceType": "Prompt cache read input tokens",
                        "feature": "On-demand Inference",
                        "usagetype": "USE1-NovaPro-cache-read-input-token-count",
                    }
                },
                "SKU_NOVA_BATCH": {
                    "attributes": {
                        "model": "Nova Pro",
                        "inferenceType": "Input tokens",
                        "feature": "Batch Inference",
                        "usagetype": "USE1-NovaPro-input-tokens-batch",
                    }
                },
                "SKU_TITAN_IN": {
                    "attributes": {
                        "inferenceType": "Input tokens",
                        "feature": "On-demand Inference",
                        "usagetype": "USE1-TitanTextG1-Lite-input-tokens",
                    }
                },
                "SKU_CLAUDE_IN": {
                    "attributes": {
                        "servicename": "Claude Sonnet 4.6 (Amazon Bedrock Edition)",
                        "model": "Claude Sonnet 4.6",
                        "inferenceType": "Input tokens",
                        "feature": "On-demand Inference",
                        "usagetype": "USE1-ClaudeSonnet4.6-input-tokens",
                    }
                },
            },
            "terms": {
                "OnDemand": {
                    "SKU_NOVA_IN": _amazon_price_dim("0.0008000000"),
                    "SKU_NOVA_OUT": _amazon_price_dim("0.0032000000"),
                    "SKU_NOVA_CACHE_READ": _amazon_price_dim("0.0002000000"),
                    "SKU_NOVA_BATCH": _amazon_price_dim("0.0004000000"),
                    "SKU_TITAN_IN": _amazon_price_dim("0.0001500000"),
                    "SKU_CLAUDE_IN": _amazon_price_dim("0.0030000000"),
                }
            },
        }

        rows = parse_amazon_bedrock(payload)

        # Third-party Claude row is rejected; batch variant is excluded.
        providers = {row["provider_id"] for row in rows}
        self.assertEqual(providers, {"aws"})
        models = sorted({row["model_id"] for row in rows})
        self.assertNotIn("claude-sonnet-4.6", models)
        self.assertEqual(models, ["nova-pro", "titan-text-g1-lite"])

        by_key = {(r["model_id"], r["component"]): r for r in rows}
        # 1K → 1M conversion (×1000).
        self.assertEqual(by_key[("nova-pro", "input")]["price"], 0.8)
        self.assertEqual(by_key[("nova-pro", "output")]["price"], 3.2)
        self.assertEqual(by_key[("nova-pro", "cached_input")]["price"], 0.2)
        self.assertEqual(by_key[("nova-pro", "input")]["unit"], "per_1M_tokens")
        self.assertEqual(by_key[("nova-pro", "input")]["service_tier"], "standard")
        # Batch variant excluded entirely.
        self.assertNotIn(("nova-pro", "input_batch"), by_key)
        self.assertEqual(len([r for r in rows if r["model_id"] == "nova-pro"]), 3)
        self.assertEqual(by_key[("titan-text-g1-lite", "input")]["price"], 0.15)


class ParseXaiTest(unittest.TestCase):
    def test_parses_text_pricing_rows(self) -> None:
        html = """
        <table>
          <tr>
            <th>Model</th>
            <th>Context</th>
            <th>Input / 1M tokens</th>
            <th>Cached input / 1M tokens</th>
            <th>Output / 1M tokens</th>
          </tr>
          <tr>
            <td>grok-4.6 (&lt; 200k prompt tokens)</td>
            <td>500k</td>
            <td>$2.00</td>
            <td>$0.50</td>
            <td>$6.00</td>
          </tr>
        </table>
        """

        rows = parse_xai(html)
        by_component = {r["component"]: r for r in rows}

        self.assertEqual(len(rows), 3)
        self.assertEqual(by_component["input"]["provider_id"], "xai")
        self.assertEqual(by_component["input"]["model_id"], "grok-4.6")
        self.assertEqual(by_component["input"]["billing_variant"], "lt-200k-prompt")
        self.assertEqual(by_component["cached_input"]["price"], 0.5)
        self.assertEqual(by_component["output"]["price"], 6.0)


class ParseDeepSeekTest(unittest.TestCase):
    def test_parses_standard_cache_and_output_rows(self) -> None:
        html = """
        <table>
          <tr><th>MODEL</th><th>deepseek-v4-flash</th><th>deepseek-v4-pro</th></tr>
          <tr><td>CONTEXT LENGTH</td><td>1M</td><td>1M</td></tr>
          <tr><td>PRICING (1)</td><td>1M INPUT TOKENS (CACHE HIT)</td><td>$0.0028</td><td>$0.003625</td></tr>
          <tr><td>1M INPUT TOKENS (CACHE MISS)</td><td>$0.14</td><td>$0.435</td></tr>
          <tr><td>1M OUTPUT TOKENS</td><td>$0.28</td><td>$0.87</td></tr>
        </table>
        """

        rows = parse_deepseek(html)
        self.assertEqual(len(rows), 6)
        flash_rows = [row for row in rows if row["model_id"] == "deepseek-v4-flash"]
        self.assertEqual(len(flash_rows), 3)
        by_component = {row["component"]: row for row in flash_rows}
        self.assertEqual(by_component["input"]["price"], 0.14)
        self.assertEqual(by_component["cached_input"]["price"], 0.0028)
        self.assertEqual(by_component["output"]["price"], 0.28)
        self.assertEqual(by_component["input"]["provider_id"], "deepseek")

    def test_rejects_third_party_model_headers(self) -> None:
        html = """
        <table>
          <tr><th>MODEL</th><th>claude-sonnet-4</th><th>gpt-5</th></tr>
          <tr><td>CONTEXT LENGTH</td><td>200k</td><td>200k</td></tr>
          <tr><td>PRICING (1)</td><td>1M INPUT TOKENS (CACHE HIT)</td><td>$1</td><td>$2</td></tr>
          <tr><td>1M INPUT TOKENS (CACHE MISS)</td><td>$3</td><td>$4</td></tr>
          <tr><td>1M OUTPUT TOKENS</td><td>$5</td><td>$6</td></tr>
        </table>
        """
        self.assertEqual(parse_deepseek(html), [])
        self.assertTrue(is_deepseek_owned_model("deepseek-v4-pro"))
        self.assertFalse(is_deepseek_owned_model("claude-sonnet-4"))
        self.assertFalse(is_deepseek_owned_model("gpt-5"))

    def test_parses_every_deepseek_column_not_just_the_first_two(self) -> None:
        """A third (or nth) family member has to land in the snapshot.

        The docs table used to be exactly flash|pro. When DeepSeek adds v4.1 as
        another column, a two-column parser would never see it, and /tokens
        would keep pinning the retired pair until someone edited TIER_CANDIDATES.
        """
        html = """
        <table>
          <tr>
            <th>MODEL</th>
            <th>deepseek-v4-flash</th>
            <th>deepseek-v4-pro</th>
            <th>deepseek-v4.1-flash</th>
            <th>deepseek-v4.1-pro</th>
          </tr>
          <tr><td>CONTEXT LENGTH</td><td>1M</td><td>1M</td><td>1M</td><td>1M</td></tr>
          <tr>
            <td>PRICING (1)</td>
            <td>1M INPUT TOKENS (CACHE HIT)</td>
            <td>$0.0028</td><td>$0.003625</td><td>$0.0028</td><td>$0.003625</td>
          </tr>
          <tr><td>1M INPUT TOKENS (CACHE MISS)</td><td>$0.14</td><td>$0.435</td><td>$0.14</td><td>$0.435</td></tr>
          <tr><td>1M OUTPUT TOKENS</td><td>$0.28</td><td>$0.87</td><td>$0.28</td><td>$0.87</td></tr>
        </table>
        """
        rows = parse_deepseek(html)
        model_ids = sorted({row["model_id"] for row in rows})
        self.assertEqual(
            model_ids,
            [
                "deepseek-v4-flash",
                "deepseek-v4-pro",
                "deepseek-v4.1-flash",
                "deepseek-v4.1-pro",
            ],
        )
        self.assertEqual(len(rows), 12)

    def test_parses_current_peak_rows_and_canonical_model_ids(self) -> None:
        """The live table has descriptor columns plus OFF-PEAK/PEAK subrows."""
        html = """
        <table>
          <tr>
            <th>MODEL</th><th></th><th></th>
            <th>deepseek-flash (1)</th><th>deepseek-v4-pro (2)</th>
          </tr>
          <tr><td>CONTEXT LENGTH</td><td>1M</td></tr>
          <tr>
            <td>PRICING (3)</td><td>1M INPUT TOKENS (CACHE HIT)</td><td>OFF-PEAK</td>
            <td>$0.003</td><td>$0.022</td>
          </tr>
          <tr><td></td><td></td><td>PEAK</td><td>$0.006</td><td>$0.044</td></tr>
          <tr>
            <td></td><td>1M INPUT TOKENS (CACHE MISS)</td><td>OFF-PEAK</td>
            <td>$0.15</td><td>$0.66</td>
          </tr>
          <tr><td></td><td></td><td>PEAK</td><td>$0.30</td><td>$1.32</td></tr>
          <tr>
            <td></td><td>1M OUTPUT TOKENS</td><td>OFF-PEAK</td>
            <td>$0.60</td><td>$1.98</td>
          </tr>
          <tr><td></td><td></td><td>PEAK</td><td>$1.20</td><td>$3.96</td></tr>
          <tr><td>Concurrency Limit (4)</td><td></td><td></td><td>2500</td><td>500</td></tr>
        </table>
        """
        rows = parse_deepseek(html)
        by_model = {}
        for row in rows:
            by_model.setdefault(row["model_id"], {})[row["component"]] = row["price"]

        self.assertEqual(
            by_model,
            {
                "deepseek-flash": {
                    "input": 0.30,
                    "cached_input": 0.006,
                    "output": 1.20,
                },
                "deepseek-v4-pro": {
                    "input": 1.32,
                    "cached_input": 0.044,
                    "output": 3.96,
                },
            },
        )
        self.assertEqual(
            {row["model_id"]: row["context_window"] for row in rows},
            {"deepseek-flash": "1M", "deepseek-v4-pro": "1M"},
        )


class ParseQwenTest(unittest.TestCase):
    def test_keeps_international_list_rows_for_selected_models(self) -> None:
        html = """
        <table>
          <tr>
            <th>Model ID</th>
            <th>Deployment scope</th>
            <th>Mode</th>
            <th>Input tokens per request</th>
            <th>Input price (per 1 million tokens)</th>
            <th>Output price (per 1 million tokens) Chain of thought + answer</th>
          </tr>
          <tr>
            <td>qwen3.7-max Currently equivalent to qwen3.7-max-2026-05-20</td>
            <td>International</td>
            <td>Non-Thinking and Thinking modes</td>
            <td>0&lt;Token≤1M</td>
            <td>List price $2.5 Limited-time 50% off</td>
            <td>List price $7.5 Limited-time 50% off</td>
          </tr>
          <tr>
            <td>qwen3.7-plus</td>
            <td>International</td>
            <td>Non-Thinking and Thinking modes</td>
            <td>0&lt;Token≤256K</td>
            <td>$0.48</td>
            <td>$1.92</td>
          </tr>
          <tr>
            <td>qwen-flash</td>
            <td>International</td>
            <td>Non-Thinking mode only</td>
            <td>0&lt;Token≤256K</td>
            <td>$0.05</td>
            <td>$0.4</td>
          </tr>
          <tr>
            <td>qwen3.7-max-2026-06-08</td>
            <td>International</td>
            <td>Non-Thinking and Thinking modes</td>
            <td>0&lt;Token≤1M</td>
            <td>$2.5</td>
            <td>$7.5</td>
          </tr>
          <tr>
            <td>qwen3.7-max</td>
            <td>Chinese mainland</td>
            <td>Non-Thinking and Thinking modes</td>
            <td>0&lt;Token≤1M</td>
            <td>$1.65</td>
            <td>$4.951</td>
          </tr>
        </table>
        """

        rows = parse_qwen(html)
        self.assertEqual(len(rows), 6)
        models = sorted({row["model_id"] for row in rows})
        self.assertEqual(models, ["qwen-flash", "qwen3.7-max", "qwen3.7-plus"])
        max_input = next(
            row for row in rows if row["model_id"] == "qwen3.7-max" and row["component"] == "input"
        )
        self.assertEqual(max_input["price"], 2.5)
        self.assertEqual(max_input["provider_id"], "qwen")


OPENAI_MARKDOWN = """
Flagship models

Standard

| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-6-astra | $10.00 | $1.00 | $12.50 | $50.00 | $20.00 | $2.00 | $25.00 | $75.00 |
| gpt-6.1-sol | $2.00 | $0.10 | $2.50 | $10.00 | $4.00 | $0.20 | $5.00 | $15.00 |
| gpt-6-sol | $2.00 | $0.20 | $2.50 | $10.00 | $4.00 | $0.40 | $5.00 | $15.00 |
| gpt-5.5 (<272K context length) | $5.00 | $0.50 | - | $30.00 | $10.00 | $1.00 | - | $45.00 |
| gpt-4o | $2.50 | $1.25 | - | $10.00 | - | - | - | - |

Fast

| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-6-astra | $20.00 | $2.00 | $25.00 | $100.00 | $40.00 | $4.00 | $50.00 | $150.00 |
"""

CLAUDE_HTML = """
<table>
  <tr><th>Model</th><th>Base tokens</th><th>Prompt caching</th></tr>
  <tr><th>Name</th><th>Input</th><th>Output</th><th>5m writes</th><th>1h writes</th><th>Hits and refreshes</th></tr>
  <tr>
    <td>Claude Fable 5.1 For demanding reasoning and long-horizon agentic work</td>
    <td>$10 / MTok</td><td>$50 / MTok</td><td>$12.50 / MTok</td><td>$20 / MTok</td><td>$0.25 / MTok</td>
  </tr>
  <tr>
    <td>Claude Opus 5.5 For long-running agentic coding and knowledge work</td>
    <td>$4 / MTok</td><td>$20 / MTok</td><td>$5 / MTok</td><td>$8 / MTok</td><td>$0.20 / MTok</td>
  </tr>
  <tr>
    <td>Claude Sonnet 5.5 The best combination of speed and intelligence</td>
    <td>$2 / MTok</td><td>$10 / MTok</td><td>$2.50 / MTok</td><td>$4 / MTok</td><td>$0.10 / MTok</td>
  </tr>
  <tr>
    <td>Claude Haiku 5.5 For high-volume, latency-sensitive tasks</td>
    <td>$0.10 / MTok for prompts up to 100,000 tokens</td><td>$0.50 / MTok</td>
    <td>$0.125 / MTok</td><td>$0.20 / MTok</td><td>$0.01 / MTok</td>
  </tr>
  <tr>
    <td>$0.50 / MTok for prompts over 100,000 tokens</td><td>$2.50 / MTok</td>
    <td>$0.625 / MTok</td><td>$1 / MTok</td><td>$0.05 / MTok</td>
  </tr>
  <tr>
    <td>Claude Mythos 5.1 </td>
    <td>$10 / MTok</td><td>$50 / MTok</td><td>$12.50 / MTok</td><td>$20 / MTok</td><td>$0.25 / MTok</td>
  </tr>
  <tr>
    <td>Claude Opus 4 </td>
    <td>$15 / MTok</td><td>$75 / MTok</td><td>$18.75 / MTok</td><td>$30 / MTok</td><td>$1.50 / MTok</td>
  </tr>
</table>
"""

VERTEX_HTML = """
<html><body>
<h2>Gemini 4</h2>
<table>
  <tr><th>Model</th><th>Type</th><th>Region</th><th>Price (/1M tokens)</th></tr>
  <tr>
    <td>Gemini 4 Argon* through November 30, 2026</td>
    <td>Input (text, image, video, audio)</td><td>Global</td><td>$2</td>
  </tr>
  <tr>
    <td></td><td>Text output (response and reasoning)</td><td>Global</td><td>$10</td>
  </tr>
  <tr>
    <td>Gemini 4 Argon (Starting December 1, 2026), Gemini 4 Argon Cyber Permissive</td>
    <td>Input (text, image, video, audio)</td><td>Global</td><td>$4</td>
  </tr>
  <tr>
    <td></td><td>Text output (response and reasoning)</td><td>Global</td><td>$20</td>
  </tr>
</table>
<table>
  <tr><th>Model</th><th>Type</th><th>Region</th><th>Price (/1M tokens)</th></tr>
  <tr>
    <td>Gemini 4 Argon* through November 30, 2026</td>
    <td>Input (text, image, video, audio)</td><td>Global</td><td>$1</td>
  </tr>
  <tr>
    <td></td><td>Text output (response and reasoning)</td><td>Global</td><td>$5</td>
  </tr>
</table>
<h2>Gemini 3</h2>
<table>
  <tr><th>Model</th><th>Type</th><th>Region</th><th>Price (/1M tokens)</th></tr>
  <tr>
    <td>Gemini 3.8 Flash* through December 31, 2026</td>
    <td>Input (text)</td><td>Global</td><td>$0.75</td>
  </tr>
  <tr>
    <td></td><td>Text output (response and reasoning)</td><td>Global</td><td>$4.50</td>
  </tr>
  <tr>
    <td>Gemini 3.8 Flash Starting January 1, 2027</td>
    <td>Input (text)</td><td>Global</td><td>$1.50</td>
  </tr>
</table>
<h2>CodeMender</h2>
<table>
  <tr><th>Model</th><th>Type</th><th>Model Price /1M tokens (USD)</th></tr>
  <tr><td>Gemini 3.1 Pro</td><td>Input tokens</td><td>$2.00</td></tr>
  <tr><td>Gemini 4 Argon* Gemini 4 Argon Cyber Permissive</td><td>Input tokens</td><td>$4.00</td></tr>
  <tr><td>Gemini 3.8 Flash *</td><td>Input tokens</td><td>$1.50</td></tr>
</table>
</body></html>
"""


def _price(rows, model_id, component, **match):
    for row in rows:
        if row["model_id"] != model_id or row["component"] != component:
            continue
        if all(row.get(key) == value for key, value in match.items()):
            return row["price"]
    return None


class CurrentCatalogAccuracyTest(unittest.TestCase):
    """Pricing pages changed shape. These fixtures are the accuracy contract."""

    AS_OF = date(2026, 10, 16)

    def test_openai_markdown_keeps_output_distinct_from_cache_writes(self) -> None:
        rows = parse_openai(OPENAI_MARKDOWN)
        self.assertEqual(
            _price(rows, "gpt-6-astra", "output", service_tier="standard", context_window="short_context"),
            50.0,
        )
        self.assertEqual(
            _price(rows, "gpt-6-astra", "output", service_tier="standard", context_window="long_context"),
            75.0,
        )
        self.assertEqual(
            _price(rows, "gpt-6-astra", "input", service_tier="standard", context_window="short_context"),
            10.0,
        )
        self.assertEqual(
            _price(rows, "gpt-6-astra", "cache_write", service_tier="standard", context_window="short_context"),
            12.5,
        )
        self.assertEqual(
            _price(rows, "gpt-6-astra", "output", service_tier="fast", context_window="short_context"),
            100.0,
        )
        models = {row["model_id"] for row in rows}
        self.assertIn("gpt-6-sol", models)
        self.assertIn("gpt-4o", models)
        self.assertIn("gpt-5.5", models)
        self.assertNotIn("gpt-5.5-272k-context-length", models)

    def test_claude_grouped_header_strips_blurbs_and_splits_haiku_context(self) -> None:
        rows = parse_claude(CLAUDE_HTML)
        self.assertEqual(_price(rows, "claude-fable-5.1", "input"), 10.0)
        self.assertEqual(_price(rows, "claude-fable-5.1", "output"), 50.0)
        self.assertEqual(_price(rows, "claude-sonnet-5.5", "input"), 2.0)
        self.assertEqual(_price(rows, "claude-sonnet-5.5", "output"), 10.0)
        self.assertEqual(
            _price(rows, "claude-haiku-5.5", "input", context_window="<=100k"),
            0.10,
        )
        self.assertEqual(
            _price(rows, "claude-haiku-5.5", "output", context_window=">100k"),
            2.50,
        )
        fable = next(row for row in rows if row["model_id"] == "claude-fable-5.1")
        self.assertEqual(fable["display_name"], "Claude Fable 5.1")
        self.assertNotIn("demanding", fable["model_id"])
        mythos = next(row for row in rows if row["model_id"] == "claude-mythos-5.1")
        self.assertIn("limited availability", mythos["display_name"].lower())
        retired = next(row for row in rows if row["model_id"] == "claude-opus-4")
        self.assertFalse(retired["is_active"])

    def test_vertex_publishes_the_in_effect_argon_and_flash_rates(self) -> None:
        rows = parse_vertex(VERTEX_HTML, as_of=self.AS_OF)
        ids = {row["model_id"] for row in rows}
        self.assertIn("gemini-4-argon", ids)
        self.assertNotIn("gemini-4-argon-cyber-permissive", ids)
        self.assertNotIn("gemini-4-argon-gemini-4-argon-cyber-permissive", ids)
        self.assertNotIn("gemini-3.1-pro", ids)
        self.assertEqual(
            _price(rows, "gemini-4-argon", "input", service_tier="standard"),
            2.0,
        )
        self.assertEqual(
            _price(rows, "gemini-4-argon", "output", service_tier="standard"),
            10.0,
        )
        self.assertEqual(
            _price(rows, "gemini-4-argon", "input", service_tier="priority"),
            1.0,
        )
        self.assertEqual(_price(rows, "gemini-3.8-flash", "input"), 0.75)
        self.assertEqual(_price(rows, "gemini-3.8-flash", "output"), 4.50)
        self.assertFalse(any(row["price"] == 1.5 and row["model_id"] == "gemini-3.8-flash" for row in rows))
        self.assertFalse(any(row["price"] == 4.0 and row["model_id"] == "gemini-4-argon" for row in rows))


class IsMicrosoftOwnedModelTest(unittest.TestCase):
    def test_accepts_phi_and_mai_text(self) -> None:
        self.assertTrue(is_microsoft_owned_model("Phi-4-mini"))
        self.assertTrue(is_microsoft_owned_model("MAI-Thinking-1 Global"))

    def test_rejects_resold_and_non_text(self) -> None:
        self.assertFalse(is_microsoft_owned_model("MAI-Image-2.5"))
        self.assertFalse(is_microsoft_owned_model("gpt-6-astra"))


class ParseMicrosoftTest(unittest.TestCase):
    def test_reads_embedded_regional_prices(self) -> None:
        html = """
        <table>
          <tr>
            <th>Models</th>
            <th>Context</th>
            <th>Input (Per 1M tokens)</th>
            <th>Output (Per 1M tokens)</th>
          </tr>
          <tr>
            <td>Phi-4-mini</td>
            <td>128K</td>
            <td><span class="price-data" data-amount='{"regional":{"us-east":0.075}}'><span class="price-value">$-</span></span></td>
            <td><span class="price-data" data-amount='{"regional":{"us-east":0.3}}'><span class="price-value">$-</span></span></td>
          </tr>
        </table>
        <table>
          <tr>
            <th>Models</th>
            <th>Input (Per 1M tokens)</th>
            <th>Cached Input (Per 1M tokens)</th>
            <th>Output (Per 1M tokens)</th>
          </tr>
          <tr>
            <td>MAI-Thinking-1 Global</td>
            <td><span class="price-data" data-amount='{"regional":{"us-east":2.0}}'></span></td>
            <td><span class="price-data" data-amount='{"regional":{"us-east":0.2}}'></span></td>
            <td><span class="price-data" data-amount='{"regional":{"us-east":8.0}}'></span></td>
          </tr>
        </table>
        """
        rows = parse_microsoft(html)
        providers = {row["provider_id"] for row in rows}
        self.assertEqual(providers, {"microsoft"})
        by_key = {(r["model_id"], r["component"]): r for r in rows}
        self.assertEqual(by_key[("phi-4-mini", "input")]["price"], 0.075)
        self.assertEqual(by_key[("mai-thinking-1-global", "output")]["price"], 8.0)
        self.assertEqual(by_key[("mai-thinking-1-global", "cached_input")]["price"], 0.2)


if __name__ == "__main__":
    unittest.main()
