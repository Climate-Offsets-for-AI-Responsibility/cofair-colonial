#!/usr/bin/env python3
"""Unit tests for additional pricing sources in scrape_pricing.py."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scrape_pricing import (
    is_amazon_owned_model,
    is_deepseek_owned_model,
    parse_amazon_bedrock,
    parse_deepseek,
    parse_qwen,
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


if __name__ == "__main__":
    unittest.main()
