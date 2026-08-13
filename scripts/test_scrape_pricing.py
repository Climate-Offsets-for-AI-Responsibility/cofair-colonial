#!/usr/bin/env python3
"""Unit tests for additional pricing sources in scrape_pricing.py."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scrape_pricing import parse_aws_bedrock, parse_xai


class ParseAwsBedrockTest(unittest.TestCase):
    def test_parses_token_components_and_tiers(self) -> None:
        payload = {
            "products": {
                "SKU_INPUT_STD": {
                    "attributes": {
                        "servicename": "Claude Sonnet 4.6 (Amazon Bedrock Edition)",
                        "usagetype": "USE1-MP:USE1_InputTokenCount_Global-Units",
                    }
                },
                "SKU_OUTPUT_STD": {
                    "attributes": {
                        "servicename": "Claude Sonnet 4.6 (Amazon Bedrock Edition)",
                        "usagetype": "USE1-MP:USE1_OutputTokenCount_Global-Units",
                    }
                },
                "SKU_INPUT_BATCH": {
                    "attributes": {
                        "servicename": "Claude Sonnet 4.6 (Amazon Bedrock Edition)",
                        "usagetype": "USE1-MP:USE1_InputTokenCount_Global_Batch-Units",
                    }
                },
                "SKU_CACHE_READ": {
                    "attributes": {
                        "servicename": "Claude Sonnet 4.6 (Amazon Bedrock Edition)",
                        "usagetype": "USE1-MP:USE1_CacheReadInputTokenCount-Units",
                    }
                },
                "SKU_CACHE_WRITE_1H": {
                    "attributes": {
                        "servicename": "Claude Sonnet 4.6 (Amazon Bedrock Edition)",
                        "usagetype": "USE1-MP:USE1_CacheWrite1hInputTokenCount_Global-Units",
                    }
                },
            },
            "terms": {
                "OnDemand": {
                    "SKU_INPUT_STD": {
                        "offer": {
                            "priceDimensions": {
                                "d1": {
                                    "unit": "1M tokens",
                                    "pricePerUnit": {"USD": "3.0000000000"},
                                    "description": "Million Input Tokens Global",
                                }
                            }
                        }
                    },
                    "SKU_OUTPUT_STD": {
                        "offer": {
                            "priceDimensions": {
                                "d1": {
                                    "unit": "1M tokens",
                                    "pricePerUnit": {"USD": "15.0000000000"},
                                    "description": "Million Response Tokens Global",
                                }
                            }
                        }
                    },
                    "SKU_INPUT_BATCH": {
                        "offer": {
                            "priceDimensions": {
                                "d1": {
                                    "unit": "1M tokens",
                                    "pricePerUnit": {"USD": "1.5000000000"},
                                    "description": "Million Batch Input Tokens Global",
                                }
                            }
                        }
                    },
                    "SKU_CACHE_READ": {
                        "offer": {
                            "priceDimensions": {
                                "d1": {
                                    "unit": "1M tokens",
                                    "pricePerUnit": {"USD": "0.3000000000"},
                                    "description": "Million Cache Read Input Tokens Regional CRIS",
                                }
                            }
                        }
                    },
                    "SKU_CACHE_WRITE_1H": {
                        "offer": {
                            "priceDimensions": {
                                "d1": {
                                    "unit": "1M tokens",
                                    "pricePerUnit": {"USD": "6.0000000000"},
                                    "description": "Million 1 hour Cache Write Input Tokens Global",
                                }
                            }
                        }
                    },
                }
            },
        }

        rows = parse_aws_bedrock(payload)
        by_component = {(r["component"], r["service_tier"], r.get("billing_variant")): r for r in rows}

        self.assertEqual(len(rows), 5)
        self.assertIn(("input", "standard", "global"), by_component)
        self.assertIn(("output", "standard", "global"), by_component)
        self.assertIn(("input", "batch", "global"), by_component)
        self.assertIn(("cache_read", "standard", "regional-cris"), by_component)
        self.assertIn(("cache_write", "standard", "1h"), by_component)
        self.assertEqual(by_component[("input", "standard", "global")]["model_id"], "claude-sonnet-4.6")
        self.assertEqual(by_component[("output", "standard", "global")]["provider_id"], "aws")


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


if __name__ == "__main__":
    unittest.main()
