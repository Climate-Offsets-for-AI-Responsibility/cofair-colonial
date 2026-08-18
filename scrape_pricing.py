import json
import os
import re
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()


CLAUDE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
VERTEX_URL = "https://cloud.google.com/vertex-ai/generative-ai/pricing"
OPENAI_URL = "https://developers.openai.com/api/docs/pricing"
XAI_URL = "https://docs.x.ai/developers/pricing"
DEEPSEEK_URL = "https://api-docs.deepseek.com/quick_start/pricing"
QWEN_URL = "https://www.alibabacloud.com/help/en/model-studio/model-pricing"
AWS_BEDROCK_PRICING_REGION = os.getenv("AWS_BEDROCK_PRICING_REGION", "us-east-1")
# Native Bedrock catalog: Amazon's own Nova/Titan models. The sibling
# `AmazonBedrockFoundationModels` catalog is exclusively resold third-party
# models (Anthropic, Cohere, Meta, AI21, …) and is deliberately NOT used —
# the Amazon index must contain only Amazon-built models.
AMAZON_BEDROCK_URL = (
    "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/"
    f"AmazonBedrock/current/{AWS_BEDROCK_PRICING_REGION}/index.json"
)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}

PRICING_JSON_PATH = Path("pricing.json")
RUN_REPORT_PATH = Path("run_report.json")

OPENAI_SERVICE_TIERS = {"Standard", "Batch", "Flex", "Priority"}
OPENAI_CATEGORIES = {
    "Flagship models": "flagship",
    "Realtime and audio generation models": "realtime_audio",
    "Image generation models": "image_generation",
    "Video generation models": "video_generation",
    "Transcription models": "transcription",
    "Specialized models": "specialized",
    "Finetuning": "fine_tuning",
}

VERTEX_SERVICE_TIERS = {"Standard", "Priority", "Flex/Batch"}
VERTEX_MODEL_GROUPS = {
    "Gemini 3": "gemini_3",
    "Gemini 2.5": "gemini_2_5",
    "Gemini 2.0": "gemini_2_0",
}

MIN_TOTAL_TIERS = 100
MIN_PROVIDER_TIERS = {
    "anthropic": 10,
    "openai": 25,
    "google": 50,
    "xai": 10,
    "aws": 5,
    "deepseek": 2,
    "qwen": 3,
}
MIN_PROVIDER_RATIO = 0.45
MIN_COMPONENT_ROWS = {
    "anthropic": 30,
    "openai": 90,
    "google": 120,
    "xai": 30,
    "aws": 18,
    "deepseek": 6,
    "qwen": 6,
}


def now_iso_z():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def http_get(url, retries=3):
    last_error = None

    for attempt in range(retries):
        try:
            print(f"FETCHING via requests: {url}")
            response = requests.get(url, headers=REQUEST_HEADERS, timeout=60)
            response.raise_for_status()

            html = response.text
            if "<html" not in html.lower():
                raise RuntimeError(f"Unexpected response body while fetching {url}")

            return html
        except Exception as exc:
            last_error = exc
            if attempt == retries - 1:
                break
            time.sleep(5)

    raise RuntimeError(f"Failed after {retries} attempts: {last_error}") from last_error


def http_get_json(url, retries=3):
    last_error = None

    for attempt in range(retries):
        try:
            print(f"FETCHING JSON via requests: {url}")
            response = requests.get(url, headers=REQUEST_HEADERS, timeout=60)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt == retries - 1:
                break
            time.sleep(5)

    raise RuntimeError(f"Failed after {retries} attempts: {last_error}") from last_error


def norm(value):
    return " ".join((value or "").strip().split())


def slugify(value):
    value = norm(value).lower()
    value = value.replace("—", "-").replace("–", "-")
    value = re.sub(r"[^a-z0-9\.\- ]+", "", value)
    value = value.replace(" ", "-")
    value = re.sub(r"-+", "-", value)
    return value.strip("-")


def money(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "")
    if re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", text):
        return float(text)
    match = re.search(r"\$([0-9]+(?:\.[0-9]+)?)", value.replace(",", ""))
    return float(match.group(1)) if match else None


def table_matrix(table):
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        row = [norm(cell.get_text(" ", strip=True)) for cell in cells]
        if any(row):
            rows.append(row)
    return rows


def fill_down_first_cell(rows, expected_len):
    filled = []
    last_first = None

    for row in rows:
        current = list(row)
        if len(current) == expected_len - 1 and last_first:
            current = [last_first] + current

        if current and current[0]:
            last_first = current[0]

        filled.append(current)

    return filled


def exact_previous_text(element, options):
    option_set = set(options)
    for tag in element.find_all_previous():
        text = norm(tag.get_text(" ", strip=True))
        if text in option_set:
            return text
    return None


def previous_heading_text(element, heading_names=("h2", "h3", "h4")):
    heading = element.find_previous(heading_names)
    return norm(heading.get_text(" ", strip=True)) if heading else None


def first_price(cells):
    for cell in cells:
        value = money(cell)
        if value is not None:
            return value
    return None


def infer_modality(label):
    label_l = label.lower()
    has_text = "text" in label_l
    has_image = "image" in label_l
    has_video = "video" in label_l
    has_audio = "audio" in label_l

    count = sum([has_text, has_image, has_video, has_audio])
    if count > 1:
        return "multimodal"
    if has_audio:
        return "audio"
    if has_image:
        return "image"
    if has_video:
        return "video"
    if has_text:
        return "text"
    return None


def build_pricing_id(
    *,
    provider_id,
    model_id,
    component,
    service_tier=None,
    context_window=None,
    modality=None,
    category=None,
    billing_variant=None,
    unit=None,
):
    parts = [
        provider_id,
        model_id,
        component,
        service_tier,
        context_window,
        modality,
        category,
        billing_variant,
        unit,
    ]
    return slugify("-".join(part for part in parts if part))


def make_row(
    *,
    model_id,
    display_name,
    provider_id,
    component,
    price,
    unit,
    currency="USD",
    service_tier=None,
    context_window=None,
    modality=None,
    category=None,
    billing_variant=None,
    is_active=True,
):
    return {
        "pricing_id": build_pricing_id(
            provider_id=provider_id,
            model_id=model_id,
            component=component,
            service_tier=service_tier,
            context_window=context_window,
            modality=modality,
            category=category,
            billing_variant=billing_variant,
            unit=unit,
        ),
        "model_id": model_id,
        "display_name": display_name,
        "provider_id": provider_id,
        "component": component,
        "price": price,
        "unit": unit,
        "currency": currency,
        "service_tier": service_tier,
        "context_window": context_window,
        "modality": modality,
        "category": category,
        "billing_variant": billing_variant,
        "is_active": is_active,
    }


PRICE_FIELD_ORDER = [
    "input",
    "cached_input",
    "output",
    "cache_read",
    "cache_write_5m",
    "cache_write_1h",
    "training",
    "generation",
]


def tier_variant_for_row(row):
    if row["component"] == "cache_write":
        return None
    return row.get("billing_variant")


def grouped_field_name(row):
    component = row["component"]
    if component == "cache_write":
        variant = slugify(row.get("billing_variant") or "")
        if variant:
            return f"cache_write_{variant}"
        return "cache_write"
    return component


def build_tier_id(
    *,
    provider_id,
    model_id,
    service_tier=None,
    context_window=None,
    modality=None,
    category=None,
    billing_variant=None,
):
    parts = [
        provider_id,
        model_id,
        service_tier,
        context_window,
        modality,
        category,
        billing_variant,
    ]
    return slugify("-".join(part for part in parts if part))


def aggregate_tier_rows(component_rows):
    grouped = {}

    for row in component_rows:
        tier_key = (
            row["provider_id"],
            row["model_id"],
            row.get("service_tier"),
            row.get("context_window"),
            row.get("modality"),
            row.get("category"),
            tier_variant_for_row(row),
        )

        if tier_key not in grouped:
            grouped[tier_key] = {
                "pricing_id": build_tier_id(
                    provider_id=row["provider_id"],
                    model_id=row["model_id"],
                    service_tier=row.get("service_tier"),
                    context_window=row.get("context_window"),
                    modality=row.get("modality"),
                    category=row.get("category"),
                    billing_variant=tier_variant_for_row(row),
                ),
                "model_id": row["model_id"],
                "display_name": row["display_name"],
                "provider_id": row["provider_id"],
                "service_tier": row.get("service_tier"),
                "context_window": row.get("context_window"),
                "modality": row.get("modality"),
                "category": row.get("category"),
                "billing_variant": tier_variant_for_row(row),
                "currency": row.get("currency", "USD"),
                "is_active": row.get("is_active", True),
            }

            for field in PRICE_FIELD_ORDER:
                grouped[tier_key][f"{field}_price"] = None
                grouped[tier_key][f"{field}_unit"] = None

        field = grouped_field_name(row)
        grouped[tier_key][f"{field}_price"] = row["price"]
        grouped[tier_key][f"{field}_unit"] = row["unit"]
        grouped[tier_key]["is_active"] = grouped[tier_key]["is_active"] and row.get("is_active", True)

    rows = list(grouped.values())
    rows.sort(
        key=lambda row: (
            row["provider_id"],
            row["display_name"].lower(),
            row.get("service_tier") or "",
            row.get("context_window") or "",
            row.get("modality") or "",
            row.get("billing_variant") or "",
        )
    )
    return rows


# Amazon-owned Bedrock model families. The Bedrock catalog also resells
# third-party models (Anthropic, Cohere, Meta, AI21, TwelveLabs, Stability,
# Writer); those are NOT Amazon's own models and must never enter the Amazon
# pricing index. Ownership is validated against these families only.
AMAZON_MODEL_FAMILIES = ("nova", "titan")

# Standard on-demand text-token components → canonical component names. Keyed on
# the exact usagetype suffix so tiered variants (batch/flex/priority/custom
# model/cross-region) are excluded and only the base standard rate is kept.
_AMAZON_TOKEN_SUFFIXES = {
    "-input-tokens": "input",
    "-output-tokens": "output",
    "-cache-read-input-token-count": "cached_input",
    "-cache-write-input-token-count": "cache_write",
}

# usagetype fragments that mark a non-standard tier or a non-text modality. Any
# of these disqualifies the row from the comparable per-1M-token index.
_AMAZON_TOKEN_EXCLUSIONS = (
    "batch",
    "flex",
    "priority",
    "custom-model",
    "cross-region",
    "global",
    "video",
    "image",
    "audio",
    "speech",
    "embeddings",
    "canvas",
    "sonic",
    "reel",
    "t2i",
    "provisioned",
)


def is_amazon_owned_model(attributes):
    """True only when the pricing row belongs to an Amazon-built model.

    Bedrock resells many third-party models; ownership is asserted from the
    `model` attribute or, for Titan text rows where `model` is unset, from the
    `usagetype` slug. `servicename` alone (e.g. "... (Amazon Bedrock Edition)")
    never confers Amazon ownership.
    """
    model = norm(attributes.get("model"))
    if model and any(fam in model.lower() for fam in AMAZON_MODEL_FAMILIES):
        return True
    usagetype = (attributes.get("usagetype") or "").lower()
    # Titan text rows carry no `model`; identify them by an Amazon family token
    # in the usagetype, guarding against resold ids that merely contain "titan".
    if re.search(r"use1-titan", usagetype):
        return True
    if re.search(r"use1-nova", usagetype):
        return True
    return False


def _clean_titan_name(raw):
    """Turn a Titan usagetype slug into a readable name.

    e.g. "TitanTextG1-Lite" → "Titan Text G1 Lite"; "TitanText-Premier" →
    "Titan Text Premier".
    """
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", raw)
    spaced = spaced.replace("-", " ")
    return norm(spaced)


def _amazon_model_name(attributes):
    model = norm(attributes.get("model"))
    if model:
        return model
    usagetype = attributes.get("usagetype") or ""
    match = re.match(r"USE1-([A-Za-z0-9.]+(?:-[A-Za-z0-9.]+)?)-(?:input|output|cache)", usagetype)
    if match:
        raw = match.group(1)
        if raw.lower().startswith("titan"):
            return _clean_titan_name(raw)
        return raw
    return None


def _amazon_token_component(usagetype):
    u = (usagetype or "").lower()
    for suffix, component in _AMAZON_TOKEN_SUFFIXES.items():
        if u.endswith(suffix):
            return component
    return None


def parse_amazon_bedrock(pricing_doc):
    """Parse Amazon's own Nova/Titan list prices from the native Bedrock catalog.

    Reads the `AmazonBedrock` offer file (not `AmazonBedrockFoundationModels`,
    which is entirely resold third-party models). Keeps only Amazon-owned models
    (validated by `is_amazon_owned_model`), standard on-demand text-token
    components, and converts the catalog's per-1K-token rate to per-1M-tokens.
    """
    products = pricing_doc.get("products", {})
    on_demand = pricing_doc.get("terms", {}).get("OnDemand", {})
    rows_out = []

    for sku, product in products.items():
        attrs = product.get("attributes", {})
        usagetype = norm(attrs.get("usagetype"))
        if not usagetype:
            continue
        if not is_amazon_owned_model(attrs):
            continue

        component = _amazon_token_component(usagetype)
        if component is None:
            continue
        if any(bad in usagetype.lower() for bad in _AMAZON_TOKEN_EXCLUSIONS):
            continue

        model_name = _amazon_model_name(attrs)
        if not model_name:
            continue

        term_group = on_demand.get(sku, {})
        for offer in term_group.values():
            for dimension in offer.get("priceDimensions", {}).values():
                unit = norm(dimension.get("unit"))
                if unit.lower() != "1k tokens":
                    continue

                per_1k = money(dimension.get("pricePerUnit", {}).get("USD"))
                if per_1k is None:
                    continue

                rows_out.append(
                    make_row(
                        model_id=slugify(model_name),
                        display_name=model_name,
                        provider_id="aws",
                        component=component,
                        price=round(per_1k * 1000, 6),
                        unit="per_1M_tokens",
                        service_tier="standard",
                        category="bedrock",
                        modality="text",
                    )
                )

    return rows_out


def _xai_prompt_threshold_variant(model_label):
    m = re.search(r"\((<|≥)\s*([0-9]+k?)\s*prompt tokens\)", model_label, flags=re.IGNORECASE)
    if not m:
        return None
    side = "lt" if m.group(1) == "<" else "gte"
    threshold = m.group(2).lower()
    return f"{side}-{threshold}-prompt"


def _xai_base_model_name(model_label):
    return re.sub(
        r"\s*\((<|≥)\s*[0-9]+k?\s*prompt tokens\)\s*",
        "",
        model_label,
        flags=re.IGNORECASE,
    ).strip()


def parse_xai(html):
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []

    def append_text_row(*, model_name, context_window, input_price, cached_price, output_price, billing_variant):
        is_active = "deprecated" not in model_name.lower()
        if input_price is not None:
            rows_out.append(
                make_row(
                    model_id=slugify(model_name),
                    display_name=model_name,
                    provider_id="xai",
                    component="input",
                    price=input_price,
                    unit="per_1M_tokens",
                    service_tier="standard",
                    context_window=context_window,
                    modality="text",
                    category="text_api",
                    billing_variant=billing_variant,
                    is_active=is_active,
                )
            )
        if cached_price is not None:
            rows_out.append(
                make_row(
                    model_id=slugify(model_name),
                    display_name=model_name,
                    provider_id="xai",
                    component="cached_input",
                    price=cached_price,
                    unit="per_1M_tokens",
                    service_tier="standard",
                    context_window=context_window,
                    modality="text",
                    category="text_api",
                    billing_variant=billing_variant,
                    is_active=is_active,
                )
            )
        if output_price is not None:
            rows_out.append(
                make_row(
                    model_id=slugify(model_name),
                    display_name=model_name,
                    provider_id="xai",
                    component="output",
                    price=output_price,
                    unit="per_1M_tokens",
                    service_tier="standard",
                    context_window=context_window,
                    modality="text",
                    category="text_api",
                    billing_variant=billing_variant,
                    is_active=is_active,
                )
            )

    for table in soup.find_all("table"):
        mat = table_matrix(table)
        if len(mat) < 2:
            continue

        # Current xAI docs structure:
        # ["Model", "Context", "Short context", "Long context"]
        # ["Input", "Cached", "Output", "Input", "Cached", "Output"]
        if (
            len(mat) >= 3
            and len(mat[0]) >= 4
            and mat[0][0].lower() == "model"
            and mat[0][1].lower() == "context"
            and "short context" in mat[0][2].lower()
            and "long context" in mat[0][3].lower()
            and len(mat[1]) >= 6
        ):
            for row in mat[2:]:
                if len(row) < 8:
                    continue
                model_label = norm(row[0])
                if not model_label or "grok" not in model_label.lower():
                    continue

                base_name = re.sub(
                    r"\s+Long context.*$",
                    "",
                    model_label,
                    flags=re.IGNORECASE,
                ).strip()
                context_window = norm(row[1]) or None

                append_text_row(
                    model_name=base_name,
                    context_window=context_window,
                    input_price=money(row[2]),
                    cached_price=money(row[3]),
                    output_price=money(row[4]),
                    billing_variant="lt-200k-prompt",
                )
                append_text_row(
                    model_name=base_name,
                    context_window=context_window,
                    input_price=money(row[5]),
                    cached_price=money(row[6]),
                    output_price=money(row[7]),
                    billing_variant="gte-200k-prompt",
                )
            continue

        # Fallback parser for single-row header table shapes.
        headers = [h.lower() for h in mat[0]]
        col_model = next((i for i, h in enumerate(headers) if h == "model"), None)
        col_context = next((i for i, h in enumerate(headers) if h == "context"), None)
        col_input = next((i for i, h in enumerate(headers) if "input / 1m tokens" in h), None)
        col_cached = next((i for i, h in enumerate(headers) if "cached input / 1m tokens" in h), None)
        col_output = next((i for i, h in enumerate(headers) if "output / 1m tokens" in h), None)

        if col_model is None or col_input is None or col_output is None:
            continue

        for row in mat[1:]:
            if len(row) <= col_model:
                continue

            model_label = norm(row[col_model])
            if not model_label or "grok" not in model_label.lower():
                continue

            base_name = _xai_base_model_name(model_label)
            billing_variant = _xai_prompt_threshold_variant(model_label)
            context_window = row[col_context] if col_context is not None and len(row) > col_context else None
            append_text_row(
                model_name=base_name,
                context_window=context_window,
                input_price=money(row[col_input]) if len(row) > col_input else None,
                cached_price=money(row[col_cached]) if col_cached is not None and len(row) > col_cached else None,
                output_price=money(row[col_output]) if len(row) > col_output else None,
                billing_variant=billing_variant,
            )

    return rows_out


def is_deepseek_owned_model(model_name):
    """True only for DeepSeek's own models (id/name starts with `deepseek-`).

    DeepSeek's docs are OpenAI/Anthropic-API compatible, but those are wire
    formats — not third-party models. Still, reject anything that isn't a
    native DeepSeek model id so a future reseller table can't leak in
    (same ownership stance as Amazon Nova/Titan).
    """
    name = norm(model_name).lower()
    return name.startswith("deepseek-") or name.startswith("deepseek ")


def parse_deepseek(html):
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []

    for table in soup.find_all("table"):
        mat = table_matrix(table)
        if len(mat) < 3:
            continue
        if not mat[0] or mat[0][0].upper() != "MODEL":
            continue
        if len(mat[0]) < 3:
            continue
        if not is_deepseek_owned_model(mat[0][1]):
            continue

        model_pairs = [
            (slugify(mat[0][1]), norm(mat[0][1]), 0),
            (slugify(mat[0][2]), norm(mat[0][2]), 1),
        ]

        context_values = [None, None]
        cache_hit_values = [None, None]
        cache_miss_values = [None, None]
        output_values = [None, None]

        for row in mat[1:]:
            label = " ".join(part.lower() for part in row[:2]) if row else ""
            if "context length" in label:
                if len(row) > 1:
                    context_values[0] = row[1]
                if len(row) > 2:
                    context_values[1] = row[2]
            elif "cache hit" in label:
                if len(row) > 2 and "pricing" in row[0].lower():
                    cache_hit_values[0] = money(row[2])
                    if len(row) > 3:
                        cache_hit_values[1] = money(row[3])
                else:
                    if len(row) > 1:
                        cache_hit_values[0] = money(row[1])
                    if len(row) > 2:
                        cache_hit_values[1] = money(row[2])
            elif "cache miss" in label:
                if len(row) > 1:
                    cache_miss_values[0] = money(row[1])
                if len(row) > 2:
                    cache_miss_values[1] = money(row[2])
            elif "output" in label:
                if len(row) > 1:
                    output_values[0] = money(row[1])
                if len(row) > 2:
                    output_values[1] = money(row[2])

        for model_id, display_name, idx in model_pairs:
            if not model_id or not display_name:
                continue
            if not is_deepseek_owned_model(display_name) and not is_deepseek_owned_model(model_id):
                continue
            is_active = "deprecated" not in display_name.lower()
            context_window = context_values[idx]

            if cache_miss_values[idx] is not None:
                rows_out.append(
                    make_row(
                        model_id=model_id,
                        display_name=display_name,
                        provider_id="deepseek",
                        component="input",
                        price=cache_miss_values[idx],
                        unit="per_1M_tokens",
                        service_tier="standard",
                        context_window=context_window,
                        modality="text",
                        category="text_api",
                        is_active=is_active,
                    )
                )
            if cache_hit_values[idx] is not None:
                rows_out.append(
                    make_row(
                        model_id=model_id,
                        display_name=display_name,
                        provider_id="deepseek",
                        component="cached_input",
                        price=cache_hit_values[idx],
                        unit="per_1M_tokens",
                        service_tier="standard",
                        context_window=context_window,
                        modality="text",
                        category="text_api",
                        is_active=is_active,
                    )
                )
            if output_values[idx] is not None:
                rows_out.append(
                    make_row(
                        model_id=model_id,
                        display_name=display_name,
                        provider_id="deepseek",
                        component="output",
                        price=output_values[idx],
                        unit="per_1M_tokens",
                        service_tier="standard",
                        context_window=context_window,
                        modality="text",
                        category="text_api",
                        is_active=is_active,
                    )
                )
        break

    return rows_out


def parse_qwen(html):
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []
    preferred_models = (
        "qwen3.7-max",
        "qwen3.7-plus",
        "qwen-flash",
    )

    for table in soup.find_all("table"):
        mat = table_matrix(table)
        if len(mat) < 2:
            continue

        headers = [cell.lower() for cell in mat[0]]
        if not headers or headers[0] != "model id":
            continue
        if not any("deployment scope" in header for header in headers):
            continue
        if not any("input price" in header for header in headers):
            continue
        if not any("output price" in header for header in headers):
            continue

        col_scope = next((i for i, h in enumerate(headers) if "deployment scope" in h), None)
        col_input = next((i for i, h in enumerate(headers) if "input price" in h), None)
        col_output = next((i for i, h in enumerate(headers) if "output price" in h), None)
        col_mode = next((i for i, h in enumerate(headers) if h == "mode"), None)
        col_range = next((i for i, h in enumerate(headers) if "input tokens per request" in h), None)

        if col_scope is None or col_input is None or col_output is None:
            continue

        for row in mat[1:]:
            if len(row) <= max(col_scope, col_input, col_output):
                continue
            model_label = norm(row[0])
            if not model_label:
                continue

            model_slug = slugify(model_label)
            if not any(model_slug.startswith(prefix) for prefix in preferred_models):
                continue

            scope = norm(row[col_scope]).lower()
            if scope != "international":
                continue

            mode = norm(row[col_mode]).lower() if col_mode is not None and len(row) > col_mode else ""
            if "batch" in mode:
                continue

            token_range = norm(row[col_range]).lower() if col_range is not None and len(row) > col_range else ""

            input_price = money(row[col_input])
            output_price = money(row[col_output])
            if input_price is None or output_price is None:
                continue

            display_name = model_label.split(" Currently equivalent to ")[0].strip()
            model_id = slugify(display_name)
            if not model_id:
                continue
            if re.search(r"-20\d{2}-\d{2}-\d{2}$", model_id):
                continue

            if model_id.startswith("qwen3.7-max"):
                if token_range and "1m" not in token_range:
                    continue
            elif model_id.startswith("qwen3.7-plus"):
                if token_range and "256k" not in token_range:
                    continue
            elif model_id.startswith("qwen-flash"):
                if token_range and "256k" not in token_range:
                    continue

            is_active = "deprecated" not in display_name.lower()
            context_window = ">200k" if "3.7" in model_id else None

            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=display_name,
                    provider_id="qwen",
                    component="input",
                    price=input_price,
                    unit="per_1M_tokens",
                    service_tier="standard",
                    context_window=context_window,
                    modality="text",
                    category="text_api",
                    is_active=is_active,
                )
            )
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=display_name,
                    provider_id="qwen",
                    component="output",
                    price=output_price,
                    unit="per_1M_tokens",
                    service_tier="standard",
                    context_window=context_window,
                    modality="text",
                    category="text_api",
                    is_active=is_active,
                )
            )

    return rows_out


def parse_claude(html):
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []

    for table in soup.find_all("table"):
        mat = table_matrix(table)
        if len(mat) < 2:
            continue

        headers = [header.lower() for header in mat[0]]
        if "base input" not in " ".join(headers) or "output" not in " ".join(headers):
            continue

        col_model = 0
        col_base_input = next((i for i, h in enumerate(headers) if "base input" in h), None)
        col_cache_write_5m = next((i for i, h in enumerate(headers) if "cache writes" in h or "5 min cache writes" in h), None)
        col_cache_write_1h = next((i for i, h in enumerate(headers) if "1 hr cache writes" in h or "1h cache writes" in h), None)
        col_cache_hit = next((i for i, h in enumerate(headers) if "cache hits" in h), None)
        col_output = next((i for i, h in enumerate(headers) if "output" in h), None)

        for row in mat[1:]:
            if len(row) <= col_model:
                continue

            name = norm(row[col_model])
            if "claude" not in name.lower():
                continue

            model_id = slugify(name)
            is_active = "deprecated" not in name.lower()

            if col_base_input is not None and len(row) > col_base_input:
                price = money(row[col_base_input])
                if price is not None:
                    rows_out.append(
                        make_row(
                            model_id=model_id,
                            display_name=name,
                            provider_id="anthropic",
                            component="input",
                            price=price,
                            unit="per_1M_tokens",
                            category="standard_api",
                            is_active=is_active,
                        )
                    )

            if col_output is not None and len(row) > col_output:
                price = money(row[col_output])
                if price is not None:
                    rows_out.append(
                        make_row(
                            model_id=model_id,
                            display_name=name,
                            provider_id="anthropic",
                            component="output",
                            price=price,
                            unit="per_1M_tokens",
                            category="standard_api",
                            is_active=is_active,
                        )
                    )

            if col_cache_write_5m is not None and len(row) > col_cache_write_5m:
                price = money(row[col_cache_write_5m])
                if price is not None:
                    rows_out.append(
                        make_row(
                            model_id=model_id,
                            display_name=name,
                            provider_id="anthropic",
                            component="cache_write",
                            price=price,
                            unit="per_1M_tokens",
                            category="standard_api",
                            billing_variant="5m",
                            is_active=is_active,
                        )
                    )

            if col_cache_write_1h is not None and len(row) > col_cache_write_1h:
                price = money(row[col_cache_write_1h])
                if price is not None:
                    rows_out.append(
                        make_row(
                            model_id=model_id,
                            display_name=name,
                            provider_id="anthropic",
                            component="cache_write",
                            price=price,
                            unit="per_1M_tokens",
                            category="standard_api",
                            billing_variant="1h",
                            is_active=is_active,
                        )
                    )

            if col_cache_hit is not None and len(row) > col_cache_hit:
                price = money(row[col_cache_hit])
                if price is not None:
                    rows_out.append(
                        make_row(
                            model_id=model_id,
                            display_name=name,
                            provider_id="anthropic",
                            component="cache_read",
                            price=price,
                            unit="per_1M_tokens",
                            category="standard_api",
                            is_active=is_active,
                        )
                    )

    return rows_out


def infer_service_tier_from_headers(headers):
    """Read Standard/Priority/Flex from column titles (tab labels are unreliable)."""
    joined = " ".join(headers).lower()
    if "flex/batch" in joined or "with flex" in joined:
        return "flexbatch"
    if "priority" in joined:
        return "priority"
    # "Price … with Batch API" is a Standard table that also exposes batch cells
    if "batch api" in joined or "200k" in joined or "1m" in joined or "token" in joined:
        return "standard"
    return None


def _vertex_context_window(header):
    header_l = header.lower()
    # Order matters: check <= before bare < / >
    if "<= 200k" in header_l or "<=200k" in header_l or "=< 200k" in header_l:
        return "<=200k"
    if "> 200k" in header_l or ">200k" in header_l:
        return ">200k"
    return None


def parse_vertex_model_tables(table, service_tier, category):
    """Parse Gemini model/type token tables.

    Current Vertex markup puts Model + Type (+ optional Region) on one row and
    fill-down blanks for continuations. Older markup used a model-only banner
    row, then Type labels in column 0 — both shapes are accepted.
    """
    mat = table_matrix(table)
    if len(mat) < 2:
        return []

    headers = [header.lower() for header in mat[0]]
    # Require Type as column 2 — excludes "Model / Feature / Type" modality tables.
    if not headers or headers[0] != "model" or headers[1] != "type":
        return []

    header_tier = infer_service_tier_from_headers(headers)
    if header_tier:
        service_tier = header_tier

    type_idx = headers.index("type")
    region_idx = headers.index("region") if "region" in headers else None
    price_start = max(type_idx + 1, (region_idx + 1) if region_idx is not None else 0)

    rows_out = []
    current_model = None
    current_label = None

    for row in mat[1:]:
        if not row:
            continue

        # Banner row (legacy): model name alone
        if row[0] and "gemini" in row[0].lower() and len(row) == 1:
            current_model = norm(row[0])
            current_label = None
            continue

        if row[0] and "gemini" in row[0].lower():
            current_model = norm(row[0])

        if not current_model:
            continue

        if region_idx is not None and region_idx < len(row):
            region = norm(row[region_idx]).lower()
            if region and "non-global" in region:
                continue

        label = ""
        if type_idx < len(row) and row[type_idx] and money(row[type_idx]) is None:
            label = norm(row[type_idx])
            current_label = label
        elif row[0] and "gemini" not in row[0].lower() and money(row[0]) is None:
            # Legacy: type/price label lived in column 0
            label = norm(row[0])
            current_label = label
        else:
            label = current_label or ""

        if not label:
            continue

        label_l = label.lower()
        # Skip modality-based reference rows — token tables only.
        if any(marker in label_l for marker in ("$/m char", "$/image", "$/sec", "per image", "per second")):
            continue

        modality = infer_modality(label)
        if "cached input" in label_l:
            component = "cached_input"
        elif "input" in label_l:
            component = "input"
        elif "output" in label_l:
            component = "output"
        elif "training" in label_l:
            component = "training"
        else:
            component = slugify(label_l) or "price"

        model_id = slugify(current_model)
        # Current markup: row width matches headers. Legacy data rows omit the
        # Model column, so price cells are shifted one left vs headers.
        shifted = len(row) == len(headers) - 1

        for header_idx in range(price_start, len(headers)):
            row_idx = header_idx - 1 if shifted else header_idx
            if row_idx < 0 or row_idx >= len(row):
                continue

            header = headers[header_idx]
            price = money(row[row_idx])
            if price is None:
                continue

            context_window = _vertex_context_window(header)
            component_name = "cached_input" if "cached input" in header else component
            cell_tier = service_tier
            if "batch api" in header:
                cell_tier = "batch"
            elif "priority" in header:
                cell_tier = "priority"
            elif "flex/batch" in header or "with flex" in header:
                cell_tier = "flexbatch"

            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=current_model,
                    provider_id="google",
                    component=component_name,
                    price=price,
                    unit="per_1M_tokens",
                    service_tier=cell_tier,
                    context_window=context_window,
                    modality=modality,
                    category=category,
                    is_active=True,
                )
            )

    return rows_out


def parse_vertex_gemini_2_token_table(table):
    mat = table_matrix(table)
    if len(mat) < 2:
        return []

    headers = [header.lower() for header in mat[0]]
    if headers[:4] != ["model", "type", "price", "price with batch api"]:
        return []

    rows_out = []
    current_model = None

    for row in mat[1:]:
        if not row:
            continue

        if len(row) == 1 and "gemini" in row[0].lower():
            current_model = norm(row[0])
            continue

        if not current_model or len(row) < 3:
            continue

        label = norm(row[0]).lower()
        if label.startswith("grounding with") or label.startswith("web grounding"):
            continue
        model_id = slugify(current_model)

        if "input" in label:
            component = "input"
        elif "output" in label:
            component = "output"
        elif "tuning" in label or "training" in label:
            component = "training"
        else:
            component = "price"

        modality = None
        if "audio" in label:
            modality = "audio"
        elif "image" in label:
            modality = "image"
        elif "video" in label:
            modality = "video"
        elif "text" in label:
            modality = "text"

        standard_price = money(row[1]) if len(row) > 1 else None
        batch_price = money(row[2]) if len(row) > 2 else None

        if standard_price is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=current_model,
                    provider_id="google",
                    component=component,
                    price=standard_price,
                    unit="per_1M_tokens",
                    service_tier="standard",
                    modality=modality,
                    category="gemini_2_0",
                )
            )

        if batch_price is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=current_model,
                    provider_id="google",
                    component=component,
                    price=batch_price,
                    unit="per_1M_tokens",
                    service_tier="batch",
                    modality=modality,
                    category="gemini_2_0",
                )
            )

    return rows_out


def parse_vertex_embedding_tables(table):
    mat = table_matrix(table)
    if len(mat) < 2:
        return []

    headers = [header.lower() for header in mat[0]]
    rows_out = []

    if headers[:4] == ["model", "type", "region", "price per 1,000 input tokens"]:
        current_model = None
        for row in fill_down_first_cell(mat[1:], len(headers)):
            if len(row) < 4:
                continue
            current_model = norm(row[0]) or current_model
            if not current_model:
                continue

            cell = norm(row[3])
            online_match = re.search(r"Online requests:\s*\$([0-9]+(?:\.[0-9]+)?)", cell)
            batch_match = re.search(r"Batch requests:\s*\$([0-9]+(?:\.[0-9]+)?)", cell)
            row_type = norm(row[1]).lower()
            component = "input" if "input" in row_type else "output"

            if online_match:
                rows_out.append(
                    make_row(
                        model_id=slugify(current_model),
                        display_name=current_model,
                        provider_id="google",
                        component=component,
                        price=float(online_match.group(1)),
                        unit="per_1K_tokens",
                        service_tier="online",
                        category="embeddings",
                    )
                )
            if batch_match:
                rows_out.append(
                    make_row(
                        model_id=slugify(current_model),
                        display_name=current_model,
                        provider_id="google",
                        component=component,
                        price=float(batch_match.group(1)),
                        unit="per_1K_tokens",
                        service_tier="batch",
                        category="embeddings",
                    )
                )

    if headers[:4] == ["model", "type", "region", "price per 1,000 characters"]:
        current_model = None
        for row in fill_down_first_cell(mat[1:], len(headers)):
            if len(row) < 4:
                continue
            current_model = norm(row[0]) or current_model
            if not current_model:
                continue

            cell = norm(row[3])
            online_match = re.search(r"Online requests:\s*\$([0-9]+(?:\.[0-9]+)?)", cell)
            batch_match = re.search(r"Batch requests:\s*\$([0-9]+(?:\.[0-9]+)?)", cell)
            row_type = norm(row[1]).lower()
            component = "input" if "input" in row_type else "output"

            if online_match:
                rows_out.append(
                    make_row(
                        model_id=slugify(current_model),
                        display_name=current_model,
                        provider_id="google",
                        component=component,
                        price=float(online_match.group(1)),
                        unit="per_1K_characters",
                        service_tier="online",
                        category="embeddings",
                    )
                )
            if batch_match:
                rows_out.append(
                    make_row(
                        model_id=slugify(current_model),
                        display_name=current_model,
                        provider_id="google",
                        component=component,
                        price=float(batch_match.group(1)),
                        unit="per_1K_characters",
                        service_tier="batch",
                        category="embeddings",
                    )
                )

    return rows_out


def parse_vertex(html):
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []

    for table in soup.find_all("table"):
        heading = previous_heading_text(table)
        # Modality-based ($/char, $/image, $/sec) tables are reference-only.
        if heading == "Modality-based pricing":
            continue

        mat = table_matrix(table)
        headers = [h.lower() for h in mat[0]] if mat else []
        header_tier = infer_service_tier_from_headers(headers) if headers else None

        model_group = exact_previous_text(table, VERTEX_MODEL_GROUPS.keys())
        if not model_group and heading in VERTEX_MODEL_GROUPS:
            model_group = heading

        # Prefer header-encoded tier; tab labels currently resolve to Flex/Batch
        # even for the Standard pane.
        service_tier = header_tier
        if not service_tier:
            tier_text = exact_previous_text(table, VERTEX_SERVICE_TIERS)
            service_tier = slugify(tier_text) if tier_text else None

        looks_like_gemini_table = (
            bool(headers)
            and headers[0] == "model"
            and headers[1] == "type"
            and bool(model_group)
        )

        if looks_like_gemini_table:
            rows_out.extend(
                parse_vertex_model_tables(
                    table=table,
                    service_tier=service_tier or "standard",
                    category=VERTEX_MODEL_GROUPS[model_group],
                )
            )
            continue

        if heading == "Token-based pricing":
            rows_out.extend(parse_vertex_gemini_2_token_table(table))
            continue

        rows_out.extend(parse_vertex_embedding_tables(table))

    allowed_components = {"input", "output", "cached_input", "training", "generation"}
    return [row for row in rows_out if row["component"] in allowed_components]


def parse_openai_flagship_table(table, service_tier, category):
    mat = table_matrix(table)
    if len(mat) < 3:
        return []

    first_row = [cell.lower() for cell in mat[0]]
    second_row = [cell.lower() for cell in mat[1]]
    if "model" not in second_row or not any("context" in cell for cell in first_row):
        return []

    rows_out = []
    for row in mat[2:]:
        if len(row) < 4:
            continue

        name = norm(row[0])
        if not name:
            continue

        model_id = slugify(name)
        short_input = money(row[1]) if len(row) > 1 else None
        short_cached = money(row[2]) if len(row) > 2 else None
        short_output = money(row[3]) if len(row) > 3 else None

        if short_input is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="input",
                    price=short_input,
                    unit="per_1M_tokens",
                    service_tier=service_tier,
                    context_window="short_context",
                    category=category,
                )
            )
        if short_cached is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="cached_input",
                    price=short_cached,
                    unit="per_1M_tokens",
                    service_tier=service_tier,
                    context_window="short_context",
                    category=category,
                )
            )
        if short_output is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="output",
                    price=short_output,
                    unit="per_1M_tokens",
                    service_tier=service_tier,
                    context_window="short_context",
                    category=category,
                )
            )

        if len(row) >= 7:
            long_input = money(row[4])
            long_cached = money(row[5])
            long_output = money(row[6])

            if long_input is not None:
                rows_out.append(
                    make_row(
                        model_id=model_id,
                        display_name=name,
                        provider_id="openai",
                        component="input",
                        price=long_input,
                        unit="per_1M_tokens",
                        service_tier=service_tier,
                        context_window="long_context",
                        category=category,
                    )
                )
            if long_cached is not None:
                rows_out.append(
                    make_row(
                        model_id=model_id,
                        display_name=name,
                        provider_id="openai",
                        component="cached_input",
                        price=long_cached,
                        unit="per_1M_tokens",
                        service_tier=service_tier,
                        context_window="long_context",
                        category=category,
                    )
                )
            if long_output is not None:
                rows_out.append(
                    make_row(
                        model_id=model_id,
                        display_name=name,
                        provider_id="openai",
                        component="output",
                        price=long_output,
                        unit="per_1M_tokens",
                        service_tier=service_tier,
                        context_window="long_context",
                        category=category,
                    )
                )

    return rows_out


def parse_openai_modality_table(table, category):
    mat = table_matrix(table)
    if len(mat) < 2:
        return []

    headers = [header.lower() for header in mat[0]]
    if headers[:5] != ["model", "modality", "input", "cached input", "output / cost"] and headers[:5] != ["model", "modality", "input", "cached input", "output"]:
        return []

    rows_out = []
    for row in fill_down_first_cell(mat[1:], len(headers)):
        if len(row) < 5:
            continue

        name = norm(row[0])
        modality = slugify(row[1]) or None
        if not name:
            continue

        model_id = slugify(name)
        input_price = money(row[2])
        cached_input_price = money(row[3])
        output_price = money(row[4])

        if input_price is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="input",
                    price=input_price,
                    unit="per_1M_tokens",
                    modality=modality,
                    category=category,
                )
            )
        if cached_input_price is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="cached_input",
                    price=cached_input_price,
                    unit="per_1M_tokens",
                    modality=modality,
                    category=category,
                )
            )
        if output_price is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="output",
                    price=output_price,
                    unit="per_1M_tokens",
                    modality=modality,
                    category=category,
                )
            )

    return rows_out


def parse_openai_video_table(table, category):
    mat = table_matrix(table)
    if len(mat) < 2:
        return []

    headers = [header.lower() for header in mat[0]]
    if headers[:5] != ["model", "size", "portrait", "landscape", "price per second"]:
        return []

    rows_out = []
    for row in fill_down_first_cell(mat[1:], len(headers)):
        if len(row) < 5:
            continue

        name = norm(row[0])
        resolution = norm(row[1]) or None
        if not name:
            continue

        price = money(row[4])
        if price is None:
            continue

        rows_out.append(
            make_row(
                model_id=slugify(name),
                display_name=name,
                provider_id="openai",
                component="generation",
                price=price,
                unit="per_second",
                modality="video",
                category=category,
                billing_variant=slugify(resolution) if resolution else None,
            )
        )

    return rows_out


def parse_openai_transcription_table(table, category):
    mat = table_matrix(table)
    if len(mat) < 2:
        return []

    headers = [header.lower() for header in mat[0]]
    if headers[:5] != ["model", "use case", "input", "output", "estimated cost"]:
        return []

    rows_out = []
    for row in mat[1:]:
        if len(row) < 4:
            continue

        name = norm(row[0])
        use_case = slugify(row[1]) or None
        if not name:
            continue

        input_price = money(row[2])
        output_price = money(row[3])

        if input_price is not None:
            rows_out.append(
                make_row(
                    model_id=slugify(name),
                    display_name=name,
                    provider_id="openai",
                    component="input",
                    price=input_price,
                    unit="per_1M_tokens",
                    category=category,
                    billing_variant=use_case,
                )
            )
        if output_price is not None:
            rows_out.append(
                make_row(
                    model_id=slugify(name),
                    display_name=name,
                    provider_id="openai",
                    component="output",
                    price=output_price,
                    unit="per_1M_tokens",
                    category=category,
                    billing_variant=use_case,
                )
            )

    return rows_out


def parse_openai_category_model_table(table, category, service_tier=None):
    mat = table_matrix(table)
    if len(mat) < 2:
        return []

    headers = [header.lower() for header in mat[0]]
    if headers[:5] != ["category", "model", "input", "cached input", "output"]:
        return []

    rows_out = []
    for row in fill_down_first_cell(mat[1:], len(headers)):
        if len(row) < 5:
            continue

        subcategory = slugify(row[0]) or None
        name = norm(row[1])
        if not name:
            continue

        input_price = money(row[2])
        cached_input_price = money(row[3])
        output_price = money(row[4])
        model_id = slugify(name)

        if input_price is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="input",
                    price=input_price,
                    unit="per_1M_tokens",
                    service_tier=service_tier,
                    category=category,
                    billing_variant=subcategory,
                )
            )
        if cached_input_price is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="cached_input",
                    price=cached_input_price,
                    unit="per_1M_tokens",
                    service_tier=service_tier,
                    category=category,
                    billing_variant=subcategory,
                )
            )
        if output_price is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="output",
                    price=output_price,
                    unit="per_1M_tokens",
                    service_tier=service_tier,
                    category=category,
                    billing_variant=subcategory,
                )
            )

    return rows_out


def parse_openai_finetuning_table(table, category, service_tier=None):
    mat = table_matrix(table)
    if len(mat) < 2:
        return []

    headers = [header.lower() for header in mat[0]]
    if headers[:5] != ["model", "training", "input", "cached input", "output"]:
        return []

    rows_out = []
    for row in mat[1:]:
        if len(row) < 5:
            continue

        name = norm(row[0])
        if not name:
            continue

        model_id = slugify(name)
        training_price = money(row[1])
        input_price = money(row[2])
        cached_input_price = money(row[3])
        output_price = money(row[4])

        if training_price is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="training",
                    price=training_price,
                    unit="per_hour",
                    service_tier=service_tier,
                    category=category,
                )
            )
        if input_price is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="input",
                    price=input_price,
                    unit="per_1M_tokens",
                    service_tier=service_tier,
                    category=category,
                )
            )
        if cached_input_price is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="cached_input",
                    price=cached_input_price,
                    unit="per_1M_tokens",
                    service_tier=service_tier,
                    category=category,
                )
            )
        if output_price is not None:
            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=name,
                    provider_id="openai",
                    component="output",
                    price=output_price,
                    unit="per_1M_tokens",
                    service_tier=service_tier,
                    category=category,
                )
            )

    return rows_out


def parse_openai(html):
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []

    for table in soup.find_all("table"):
        category_text = exact_previous_text(table, OPENAI_CATEGORIES.keys())
        category = OPENAI_CATEGORIES.get(category_text)
        service_tier_text = exact_previous_text(table, OPENAI_SERVICE_TIERS)
        service_tier = slugify(service_tier_text) if service_tier_text else None

        rows = parse_openai_flagship_table(table, service_tier=service_tier, category=category)
        if rows:
            rows_out.extend(rows)
            continue

        rows = parse_openai_modality_table(table, category=category)
        if rows:
            rows_out.extend(rows)
            continue

        rows = parse_openai_video_table(table, category=category)
        if rows:
            rows_out.extend(rows)
            continue

        rows = parse_openai_transcription_table(table, category=category)
        if rows:
            rows_out.extend(rows)
            continue

        rows = parse_openai_category_model_table(table, category=category, service_tier=service_tier)
        if rows:
            rows_out.extend(rows)
            continue

        rows = parse_openai_finetuning_table(table, category=category, service_tier=service_tier)
        if rows:
            rows_out.extend(rows)
            continue

    return rows_out


def send_slack(text):
    label = os.environ.get("COFAIR_OPS_SOURCE", "colonial-daily-scrape")
    if not text.strip().startswith("[COFAIR ops |"):
        text = f"[COFAIR ops | {label}]\n{text}"
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL", "#notifications")

    if not token:
        print("SLACK WARNING: Missing SLACK_BOT_TOKEN; skipping Slack notification.")
        return False

    try:
        response = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "channel": channel,
                "text": text,
            },
            timeout=20,
        )
        payload = response.json()
        if not payload.get("ok"):
            print(f"SLACK WARNING: Slack API returned an error: {payload}")
            return False
    except Exception as exc:
        print(f"SLACK WARNING: Failed to send Slack notification: {exc}")
        return False

    return True


def _workflow_run_url():
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return None


def format_ops_slack_message(headline, run_id, extra_lines=None, pipeline=None):
    label = os.environ.get("COFAIR_OPS_SOURCE", pipeline or "colonial-daily-scrape")
    lines = [f"[COFAIR ops | {label}] {headline}", f"run_id: {run_id}"]
    url = _workflow_run_url()
    if url:
        lines.append(f"workflow: {url}")
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        lines.append(f"commit: {sha[:12]}")
    if extra_lines:
        lines.extend(extra_lines)
    lines.append(f"Time: {now_iso_z()}")
    return "\n".join(lines)


def write_ops_incident(envelope):
    incidents_dir = Path("ops/incidents")
    incidents_dir.mkdir(parents=True, exist_ok=True)
    latest = incidents_dir / "latest.json"
    latest.write_text(json.dumps(envelope, indent=2))
    if envelope.get("status") in ("failed", "degraded", "escalated"):
        day = envelope["finished_at"][:10]
        history_dir = incidents_dir / day
        history_dir.mkdir(parents=True, exist_ok=True)
        history_path = history_dir / f"{envelope['run_id']}.json"
        history_path.write_text(json.dumps(envelope, indent=2))


def classify_ops_error(message):
    text = (message or "").strip()
    key_match = re.search(r"KeyError:\s*['\"]?([^'\"]+)['\"]?", text)
    if key_match:
        return f"KeyError:{key_match.group(1)}", "parse"
    if re.search(r"sanity check failed", text, re.I):
        return "SanityCheckFailed", "parse"
    first = re.sub(r"[^a-zA-Z0-9:_-]+", "_", text.split("\n")[0][:120]).strip("_")[:80]
    return first or "UnknownError", "unknown"


def detect_changes(old_rows, new_rows):
    old_map = {row["pricing_id"]: row for row in old_rows}
    new_map = {row["pricing_id"]: row for row in new_rows}
    tracked_fields = [
        "is_active",
        "currency",
        "input_price",
        "input_unit",
        "cached_input_price",
        "cached_input_unit",
        "output_price",
        "output_unit",
        "cache_read_price",
        "cache_read_unit",
        "cache_write_5m_price",
        "cache_write_5m_unit",
        "cache_write_1h_price",
        "cache_write_1h_unit",
        "training_price",
        "training_unit",
        "generation_price",
        "generation_unit",
    ]

    changes = []

    for pricing_id, new_row in new_map.items():
        old_row = old_map.get(pricing_id)
        if old_row is None:
            changes.append(("NEW_TIER", new_row))
            continue

        if any(new_row.get(field) != old_row.get(field) for field in tracked_fields):
            changes.append(("PRICE_CHANGED", new_row, old_row))

    for pricing_id, old_row in old_map.items():
        if pricing_id not in new_map:
            changes.append(("REMOVED_TIER", old_row))

    return changes


def describe_row(row):
    parts = [row["display_name"]]
    if row.get("service_tier"):
        parts.append(row["service_tier"])
    if row.get("context_window"):
        parts.append(row["context_window"])
    if row.get("modality"):
        parts.append(row["modality"])
    if row.get("billing_variant"):
        parts.append(row["billing_variant"])
    return " | ".join(parts)


def format_changes(changes):
    price_fields = [
        "input_price",
        "cached_input_price",
        "output_price",
        "cache_read_price",
        "cache_write_5m_price",
        "cache_write_1h_price",
        "training_price",
        "generation_price",
    ]
    lines = []
    for change in changes:
        if change[0] == "NEW_TIER":
            row = change[1]
            details = []
            for field in price_fields:
                if row.get(field) is not None:
                    unit = row.get(field.replace("_price", "_unit"))
                    details.append(f"{field}: ${row[field]} {unit}")
            lines.append(f"• {describe_row(row)}\n  " + "; ".join(details))
        elif change[0] == "PRICE_CHANGED":
            new_row, old_row = change[1], change[2]
            field_changes = []
            for field in price_fields:
                if new_row.get(field) != old_row.get(field):
                    unit_field = field.replace("_price", "_unit")
                    field_changes.append(
                        f"{field}: ${old_row.get(field)} -> ${new_row.get(field)} ({new_row.get(unit_field)})"
                    )
            if not field_changes:
                field_changes.append("metadata changed")
            lines.append(
                f"• {describe_row(new_row)}\n"
                f"  " + "; ".join(field_changes)
            )
        elif change[0] == "REMOVED_TIER":
            row = change[1]
            lines.append(f"• {describe_row(row)}")
    return "\n".join(lines)


def dedupe_component_rows(rows):
    deduped = {}
    for row in rows:
        deduped[row["pricing_id"]] = row

    clean = list(deduped.values())
    clean.sort(
        key=lambda row: (
            row["provider_id"],
            row["display_name"].lower(),
            row["component"],
            row.get("service_tier") or "",
            row.get("context_window") or "",
            row.get("modality") or "",
            row.get("billing_variant") or "",
            row.get("unit") or "",
        )
    )
    return clean


def build_pricing_doc(rows):
    return {
        "meta": {
            "last_run_datetime": now_iso_z(),
            "schema_version": "2.1.0",
            "description": "Tier-aware pricing rows with explicit input and output token prices.",
        },
        "providers": [
            {
                "provider_id": "anthropic",
                "name": "Anthropic",
                "pricing_source": CLAUDE_URL,
            },
            {
                "provider_id": "openai",
                "name": "OpenAI",
                "pricing_source": OPENAI_URL,
            },
            {
                "provider_id": "google",
                "name": "Google",
                "pricing_source": VERTEX_URL,
            },
            {
                "provider_id": "xai",
                "name": "xAI",
                "pricing_source": XAI_URL,
            },
            {
                "provider_id": "aws",
                "name": "Amazon",
                "pricing_source": AMAZON_BEDROCK_URL,
            },
            {
                "provider_id": "deepseek",
                "name": "DeepSeek",
                "pricing_source": DEEPSEEK_URL,
            },
            {
                "provider_id": "qwen",
                "name": "Qwen (Alibaba Model Studio)",
                "pricing_source": QWEN_URL,
            },
        ],
        "pricing": rows,
    }


def _tier_sort_key(row):
    """Sort key for aggregated tier rows (no component-level `unit` field)."""
    return (
        row["provider_id"],
        row["display_name"].lower(),
        row.get("service_tier") or "",
        row.get("context_window") or "",
        row.get("modality") or "",
        row.get("billing_variant") or "",
    )


def _pricing_sort_key(row):
    return (
        row["provider_id"],
        row["display_name"].lower(),
        row.get("component") or "",
        row.get("service_tier") or "",
        row.get("context_window") or "",
        row.get("modality") or "",
        row.get("billing_variant") or "",
        row.get("unit") or "",
    )


def provider_counts(rows):
    return dict(Counter(row["provider_id"] for row in rows))


def evaluate_sanity(rows, old_rows, old_schema_version):
    counts = Counter(row["provider_id"] for row in rows)
    old_counts = Counter(row["provider_id"] for row in old_rows)
    issues = []

    if len(rows) < MIN_TOTAL_TIERS:
        issues.append(
            f"total rows {len(rows)} below minimum floor {MIN_TOTAL_TIERS}"
        )

    if (
        old_rows
        and old_schema_version == "2.1.0"
        and len(rows) < (len(old_rows) * 0.5)
    ):
        issues.append(
            f"total rows {len(rows)} dropped below 50% of previous snapshot ({len(old_rows)})"
        )

    for provider, floor in MIN_PROVIDER_TIERS.items():
        current = counts.get(provider, 0)
        if current < floor:
            issues.append(f"{provider} rows {current} below provider floor {floor}")

        previous = old_counts.get(provider, 0)
        if (
            previous
            and old_schema_version == "2.1.0"
            and current < max(1, int(previous * MIN_PROVIDER_RATIO))
        ):
            issues.append(
                f"{provider} rows {current} below {int(MIN_PROVIDER_RATIO * 100)}% "
                f"of previous snapshot ({previous})"
            )

    return {
        "issues": issues,
        "counts": dict(counts),
        "old_counts": dict(old_counts),
    }


def parse_with_retry(provider_id, url, parser, fetch_fn=http_get):
    payload = fetch_fn(url)
    rows = parser(payload)
    attempts = [len(rows)]
    floor = MIN_COMPONENT_ROWS.get(provider_id, 0)

    if floor and len(rows) < floor:
        retry_payload = fetch_fn(url)
        retry_rows = parser(retry_payload)
        attempts.append(len(retry_rows))
        if len(retry_rows) >= len(rows):
            rows = retry_rows
            payload = retry_payload

    diagnostics = {
        "component_counts": attempts,
        "final_component_rows": len(rows),
        "component_floor": floor,
        "retry_used": len(attempts) > 1,
    }
    return rows, diagnostics, payload


def remediate_with_previous_rows(rows, old_rows, old_schema_version):
    if not old_rows or old_schema_version != "2.1.0":
        return rows, {"applied": False, "providers": []}

    current = Counter(row["provider_id"] for row in rows)
    previous = Counter(row["provider_id"] for row in old_rows)
    fallback_providers = []

    for provider, prev_count in previous.items():
        if provider not in MIN_PROVIDER_TIERS:
            continue
        expected = max(MIN_PROVIDER_TIERS.get(provider, 0), int(prev_count * MIN_PROVIDER_RATIO))
        if current.get(provider, 0) < expected:
            fallback_providers.append(provider)

    if not fallback_providers:
        return rows, {"applied": False, "providers": []}

    merged = [row for row in rows if row["provider_id"] not in fallback_providers]
    merged.extend(row for row in old_rows if row["provider_id"] in fallback_providers)
    merged.sort(key=_tier_sort_key)

    remediation = {
        "applied": True,
        "providers": sorted(fallback_providers),
        "message": "Fell back to previous snapshot rows for low-count providers.",
        "counts_before": dict(current),
        "counts_after": provider_counts(merged),
    }
    return merged, remediation


def main():
    run_id = str(uuid.uuid4())
    started_at = now_iso_z()
    parse_diagnostics = {}
    sanity = {}
    remediation = {"applied": False, "providers": []}

    try:
        provider_specs = [
            ("anthropic", CLAUDE_URL, parse_claude, http_get),
            ("google", VERTEX_URL, parse_vertex, http_get),
            ("openai", OPENAI_URL, parse_openai, http_get),
            ("xai", XAI_URL, parse_xai, http_get),
            ("aws", AMAZON_BEDROCK_URL, parse_amazon_bedrock, http_get_json),
            ("deepseek", DEEPSEEK_URL, parse_deepseek, http_get),
            ("qwen", QWEN_URL, parse_qwen, http_get),
        ]

        component_rows = []
        for provider_id, url, parser, fetch_fn in provider_specs:
            rows, diagnostics, _payload = parse_with_retry(
                provider_id,
                url,
                parser,
                fetch_fn=fetch_fn,
            )
            parse_diagnostics[provider_id] = diagnostics
            component_rows.extend(rows)

        deduped_components = dedupe_component_rows(component_rows)
        clean = aggregate_tier_rows(deduped_components)

        old_rows = []
        old_schema_version = None
        if PRICING_JSON_PATH.exists():
            try:
                old_doc = json.loads(PRICING_JSON_PATH.read_text())
                old_rows = old_doc.get("pricing", [])
                old_schema_version = old_doc.get("meta", {}).get("schema_version")
            except json.JSONDecodeError:
                old_rows = []
                old_schema_version = None

        sanity = evaluate_sanity(clean, old_rows, old_schema_version)
        if sanity["issues"]:
            clean, remediation = remediate_with_previous_rows(clean, old_rows, old_schema_version)
            if remediation.get("applied"):
                sanity = evaluate_sanity(clean, old_rows, old_schema_version)

        if sanity["issues"]:
            bullet_list = "\n".join(f"- {issue}" for issue in sanity["issues"])
            raise RuntimeError(f"Sanity check failed:\n{bullet_list}")

        pricing_doc = build_pricing_doc(clean)
        PRICING_JSON_PATH.write_text(json.dumps(pricing_doc, indent=2))

        run_status = "degraded" if remediation.get("applied") else "success"
        send_slack(
            format_ops_slack_message(
                f"Pricing scrape {run_status.upper()}",
                run_id,
                [f"Rows Saved: {len(clean)}"],
            )
        )
        if remediation.get("applied"):
            send_slack(
                format_ops_slack_message(
                    "⚠️ Pricing scrape self-heal fallback applied (DEGRADED)",
                    run_id,
                    [
                        f"Providers: {', '.join(remediation['providers'])}",
                        "Using previous snapshot rows for affected providers; parser update recommended.",
                    ],
                )
            )

        changes = detect_changes(old_rows, clean)
        if changes:
            new_tiers = [change for change in changes if change[0] == "NEW_TIER"]
            price_changes = [change for change in changes if change[0] == "PRICE_CHANGED"]
            removed_tiers = [change for change in changes if change[0] == "REMOVED_TIER"]

            blocks = []
            if price_changes:
                blocks.append("💰 *PRICING CHANGES DETECTED*")
                blocks.append(format_changes(price_changes))
            if new_tiers:
                if blocks:
                    blocks.append("---")
                blocks.append("✨ *NEW PRICING TIERS DETECTED*")
                blocks.append(format_changes(new_tiers))
            if removed_tiers:
                if blocks:
                    blocks.append("---")
                blocks.append("❌ *PRICING TIERS REMOVED*")
                blocks.append(format_changes(removed_tiers))

            send_slack("\n\n".join(blocks))
        else:
            send_slack("✅ No changes detected (Pricing and tier list stable).")

        RUN_REPORT_PATH.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "started_at": started_at,
                    "finished_at": now_iso_z(),
                    "status": run_status,
                    "error": None,
                    "provider_component_counts": parse_diagnostics,
                    "sanity": sanity,
                    "remediation": remediation,
                },
                indent=2,
            )
        )
        signature, category = classify_ops_error("")
        success_incident = {
            "schema_version": "1.0.0",
            "incident_id": str(uuid.uuid4()),
            "run_id": run_id,
            "pipeline": "colonial-daily-scrape",
            "repo": os.environ.get("GITHUB_REPOSITORY", "cofair-colonial"),
            "workflow": os.environ.get("GITHUB_WORKFLOW", "Daily Pricing Scraper"),
            "status": run_status,
            "started_at": started_at,
            "finished_at": now_iso_z(),
            "commit_sha": os.environ.get("GITHUB_SHA"),
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "workflow_run_url": _workflow_run_url(),
            "artifact_links": [_workflow_run_url() + "#artifacts"] if _workflow_run_url() else [],
            "remediation": {
                "attempted": remediation.get("applied", False),
                "actions": remediation.get("providers", []),
                "result": "success" if remediation.get("applied") else None,
                "autonomy_tier": 0,
            },
            "verification": {"passed": run_status == "success", "checks": []},
            "context": {"row_count": len(clean)},
        }
        write_ops_incident(success_incident)
        print(json.dumps({"event": "pricing_update_success", "run_id": run_id, "error": None, "status": run_status}))
        # Degraded is a successful execution with stale-provider fallback. Keep it
        # visible via incident status + Slack, but do not fail the workflow.
        return 0

    except Exception as exc:
        error = str(exc)
        print(f"CRITICAL ERROR: {error}")
        signature, category = classify_ops_error(error)
        send_slack(
            format_ops_slack_message(
                "Pricing scrape FAILED",
                run_id,
                [f"Error: {error}", f"signature: {signature} ({category})"],
            )
        )
        RUN_REPORT_PATH.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "started_at": started_at,
                    "finished_at": now_iso_z(),
                    "status": "failed",
                    "error": error,
                    "error_signature": signature,
                    "provider_component_counts": parse_diagnostics,
                    "sanity": sanity,
                    "remediation": remediation,
                },
                indent=2,
            )
        )
        write_ops_incident(
            {
                "schema_version": "1.0.0",
                "incident_id": str(uuid.uuid4()),
                "run_id": run_id,
                "pipeline": "colonial-daily-scrape",
                "repo": os.environ.get("GITHUB_REPOSITORY", "cofair-colonial"),
                "workflow": os.environ.get("GITHUB_WORKFLOW", "Daily Pricing Scraper"),
                "status": "failed",
                "started_at": started_at,
                "finished_at": now_iso_z(),
                "error_message": error,
                "error_signature": signature,
                "error_category": category,
                "commit_sha": os.environ.get("GITHUB_SHA"),
                "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
                "workflow_run_url": _workflow_run_url(),
                "artifact_links": [_workflow_run_url() + "#artifacts"] if _workflow_run_url() else [],
                "log_excerpt": error[:4000],
                "remediation": {
                    "attempted": False,
                    "actions": [],
                    "result": None,
                    "autonomy_tier": 0,
                },
                "verification": {"passed": False, "checks": []},
            }
        )
        print(json.dumps({"event": "pricing_update_failed", "run_id": run_id, "error": error, "signature": signature}))
        return 1

if __name__ == "__main__":
    sys.exit(main())
