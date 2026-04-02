import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()


CLAUDE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
VERTEX_URL = "https://cloud.google.com/vertex-ai/generative-ai/pricing"
OPENAI_URL = "https://developers.openai.com/api/docs/pricing"
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


def parse_vertex_model_tables(table, service_tier, category):
    mat = table_matrix(table)
    if len(mat) < 2:
        return []

    headers = [header.lower() for header in mat[0]]
    if not headers or headers[0] != "model" or len(headers) < 3 or headers[1] != "type":
        return []

    rows_out = []
    current_model = None

    for row in mat[1:]:
        if not row:
            continue

        if "gemini" in row[0].lower() and (len(row) == 1 or len(row) > 4):
            current_model = norm(row[0])
            continue

        if not current_model:
            continue

        label = norm(row[0])
        if not label:
            continue

        model_id = slugify(current_model)
        component = "price"
        modality = infer_modality(label)

        label_l = label.lower()
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

        for header_idx, header in enumerate(headers[2:], start=2):
            row_idx = header_idx - 1
            if row_idx >= len(row):
                continue

            price = money(row[row_idx])
            if price is None:
                continue

            context_window = None
            if "<= 200k" in header or "<=200k" in header:
                context_window = "<=200k"
            elif "> 200k" in header or ">200k" in header:
                context_window = ">200k"

            component_name = component
            if "cached input" in header:
                component_name = "cached_input"

            rows_out.append(
                make_row(
                    model_id=model_id,
                    display_name=current_model,
                    provider_id="google",
                    component=component_name,
                    price=price,
                    unit="per_1M_tokens",
                    service_tier=service_tier,
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
        service_tier = exact_previous_text(table, VERTEX_SERVICE_TIERS)
        model_group = exact_previous_text(table, VERTEX_MODEL_GROUPS.keys())

        if service_tier and model_group:
            rows_out.extend(
                parse_vertex_model_tables(
                    table=table,
                    service_tier=slugify(service_tier),
                    category=VERTEX_MODEL_GROUPS[model_group],
                )
            )
            continue

        heading = previous_heading_text(table)
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
            row["unit"],
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
        ],
        "pricing": rows,
    }


def main():
    run_id = str(uuid.uuid4())
    started_at = now_iso_z()

    try:
        claude_html = http_get(CLAUDE_URL)
        vertex_html = http_get(VERTEX_URL)
        openai_html = http_get(OPENAI_URL)

        component_rows = parse_claude(claude_html) + parse_vertex(vertex_html) + parse_openai(openai_html)
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

        if len(clean) < 100:
            raise RuntimeError(
                f"Sanity check failed: scraped only {len(clean)} pricing tiers, which is below the minimum expected floor."
            )

        if (
            old_rows
            and old_schema_version == "2.1.0"
            and len(clean) < (len(old_rows) * 0.5)
        ):
            raise RuntimeError(
                f"Sanity check failed: scraped only {len(clean)} pricing tiers, expected roughly {len(old_rows)}."
            )

        pricing_doc = build_pricing_doc(clean)
        PRICING_JSON_PATH.write_text(json.dumps(pricing_doc, indent=2))

        send_slack(
            "Pricing scrape SUCCESS\n"
            f"Rows Saved: {len(clean)}\n"
            f"Time: {now_iso_z()}"
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
                    "status": "success",
                    "error": None,
                },
                indent=2,
            )
        )
        print(json.dumps({"event": "pricing_update_success", "run_id": run_id, "error": None}))
        return 0

    except Exception as exc:
        error = str(exc)
        print(f"CRITICAL ERROR: {error}")
        send_slack(
            "Pricing scrape FAILED\n"
            f"Error: {error}\n"
            f"Time: {now_iso_z()}"
        )
        RUN_REPORT_PATH.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "started_at": started_at,
                    "finished_at": now_iso_z(),
                    "status": "failed",
                    "error": error,
                },
                indent=2,
            )
        )
        print(json.dumps({"event": "pricing_update_failed", "run_id": run_id, "error": error}))
        return 1

if __name__ == "__main__":
    sys.exit(main())
