import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import os

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from dotenv import load_dotenv
load_dotenv()


CLAUDE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
VERTEX_URL = "https://cloud.google.com/vertex-ai/generative-ai/pricing"
OPENAI_URL = "https://platform.openai.com/docs/pricing" 

PRICING_JSON_PATH = Path("pricing.json")
RUN_REPORT_PATH = Path("run_report.json")


#utilities

def now_iso_z():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def http_get(url):
    with sync_playwright() as p:
        # Launch browser (headless=True for background, False to watch it work)
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print(f"FETCHING via Playwright: {url}")
        
        try:
            # Increase timeout for heavy documentation pages
            page.goto(url, wait_until="networkidle", timeout=60000)
            
            # Scroll to bottom to trigger any lazy-loading tables
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            
            # Wait a moment for any JS-driven tables to finish rendering
            page.wait_for_timeout(2000) 
            
            html = page.content()
            browser.close()
            return html
            
        except Exception as e:
            browser.close()
            raise RuntimeError(f"Playwright failed to fetch {url}: {str(e)}")

def norm(s):
    return " ".join((s or "").strip().split())


def money(s):
    if not s:
        return None
    m = re.search(r"\$([0-9]+(?:\.[0-9]+)?)", s.replace(",", ""))
    return float(m.group(1)) if m else None


def model_id_from_name(name):
    x = norm(name).lower()
    x = x.replace("—", "-").replace("–", "-")
    x = re.sub(r"[^a-z0-9\.\- ]+", "", x)
    x = x.replace(" ", "-")
    x = re.sub(r"-+", "-", x)
    return x


def table_matrix(table):
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        row = [norm(c.get_text(" ", strip=True)) for c in cells]
        if any(row):
            rows.append(row)
    return rows


#anthropic

def parse_claude(html):
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []

    for table in soup.find_all("table"):
        mat = table_matrix(table)
        if len(mat) < 2:
            continue

        headers = [h.lower() for h in mat[0]]
        header_text = " ".join(headers)

        if "base input" not in header_text or "output" not in header_text:
            continue

        col_model = 0
        col_input = next(i for i, h in enumerate(headers) if "base input" in h)
        col_output = next(i for i, h in enumerate(headers) if "output" in h)

        for r in mat[1:]:
            name = r[col_model]
            if "claude" not in name.lower():
                continue

            model_id = model_id_from_name(name)

            is_active = "deprecated" not in name.lower()

            rows_out.append({
                "model_id": model_id,
                "display_name": name,
                "provider_id": "anthropic",
                "type": "chat",
                "unit": "per_1M_tokens",
                "currency": "USD",
                "input_price": money(r[col_input]),
                "output_price": money(r[col_output]),
                "context_window": None,
                "is_active": is_active
            })

    return rows_out


#vertex

def parse_vertex(html):
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []

    for table in soup.find_all("table"):

        mat = table_matrix(table)
        if len(mat) < 2:
            continue

        headers = [h.lower() for h in mat[0]]

        current_model = None
        base_id = None

        for r in mat[1:]:

            if not r:
                continue

            first = r[0].lower()

            # detect model header rows
            if "gemini" in first:
                current_model = r[0]
                base_id = model_id_from_name(current_model)
                continue

            if not current_model:
                continue

            row_text = " ".join(r).lower()

            if "grounding" in row_text:
                continue

            # detect prices in row
            prices = [money(x) for x in r]

            prices = [p for p in prices if p is not None]

            if not prices:
                continue

            label = norm(r[0] if len(r) == 1 else r[1])

            label_id = model_id_from_name(label)

            input_price = None
            output_price = None

            if "input" in label.lower():
                input_price = prices[0]

            elif "output" in label.lower():
                output_price = prices[0]

            else:
                output_price = prices[0]

            rows_out.append({
                "model_id": f"{base_id}-{label_id}",
                "display_name": f"{current_model} ({label})",
                "provider_id": "google",
                "type": "chat",
                "unit": "per_1M_tokens",
                "currency": "USD",
                "input_price": input_price,
                "output_price": output_price,
                "context_window": None,
                "is_active": True
            })

    return rows_out

# openai

def parse_openai(html):
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []

    for table in soup.find_all("table"):

        mat = table_matrix(table)
        if len(mat) < 2:
            continue

        headers = [h.lower() for h in mat[0]]
        header_text = " ".join(headers)

        if "model" not in header_text:
            continue

        col_model = 0

        col_input = next((i for i, h in enumerate(headers) if "input" in h), None)
        col_cached = next((i for i, h in enumerate(headers) if "cached" in h), None)
        col_output = next((i for i, h in enumerate(headers) if "output" in h), None)
        col_cost = next((i for i, h in enumerate(headers) if "cost" in h or "price" in h), None)

        for r in mat[1:]:

            if len(r) <= col_model:
                continue

            name = r[col_model]
            if not name:
                continue

            model_id = model_id_from_name(name)

            input_price = money(r[col_input]) if col_input is not None and len(r) > col_input else None
            cached_price = money(r[col_cached]) if col_cached is not None and len(r) > col_cached else None
            output_price = money(r[col_output]) if col_output is not None and len(r) > col_output else None

            if col_cost is not None and input_price is None and output_price is None:
                output_price = money(r[col_cost])

            rows_out.append({
                "model_id": model_id,
                "display_name": name,
                "provider_id": "openai",
                "type": "chat",
                "unit": "per_1M_tokens",
                "currency": "USD",
                "input_price": input_price,
                "output_price": output_price,
                "context_window": None,
                "is_active": True
            })

            if cached_price is not None:

                rows_out.append({
                    "model_id": f"{model_id}-cached-input",
                    "display_name": f"{name} (Cached input)",
                    "provider_id": "openai",
                    "type": "cache",
                    "unit": "per_1M_tokens",
                    "currency": "USD",
                    "input_price": cached_price,
                    "output_price": 0.0,
                    "context_window": None,
                    "is_active": True
                })

    return rows_out

def send_slack(text):
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL", "#notifications")

    if not token:
        raise RuntimeError("Missing SLACK_BOT_TOKEN")

    r = requests.post(
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

    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack error: {data}")
    

def detect_changes(old_rows, new_rows):
    old_map = {
        (r["provider_id"], r["model_id"], r["type"]): r
        for r in old_rows
    }
    new_map = {
        (r["provider_id"], r["model_id"], r["type"]): r
        for r in new_rows
    }

    changes = []

    for key, new_r in new_map.items():
        old_r = old_map.get(key)

        if old_r is None:
            changes.append(("NEW_MODEL", new_r))
            continue

        if (
            new_r["input_price"] != old_r["input_price"]
            or new_r["output_price"] != old_r["output_price"]
            or new_r["context_window"] != old_r["context_window"]
            or new_r["is_active"] != old_r["is_active"]
        ):
            changes.append(("PRICE_CHANGED", new_r, old_r))

    for key, old_r in old_map.items():
        if key not in new_map:
            changes.append(("REMOVED_MODEL", old_r))

    return changes


def format_changes(changes):
    lines = []

    for c in changes:
        if c[0] == "NEW_MODEL":
            r = c[1]
            lines.append(
                f"New model: {r['display_name']} "
                f"(input ${r['input_price']}, output ${r['output_price']})"
            )

        elif c[0] == "PRICE_CHANGED":
            new_r, old_r = c[1], c[2]
            lines.append(
                f"💰 Price changed: {new_r['display_name']}\n"
                f"   Input: {old_r['input_price']} → {new_r['input_price']}\n"
                f"   Output: {old_r['output_price']} → {new_r['output_price']}"
            )

        elif c[0] == "REMOVED_MODEL":
            r = c[1]
            lines.append(f"Removed model: {r['display_name']}")

    return "\n".join(lines)



# main

def main():
    run_id = str(uuid.uuid4())
    start = now_iso_z()

    status = "success"
    error = None

    try:
        claude_html = http_get(CLAUDE_URL)
        vertex_html = http_get(VERTEX_URL)
        openai_html = http_get(OPENAI_URL)

        rows = (parse_claude(claude_html) + parse_vertex(vertex_html) + parse_openai(openai_html))

        old_rows = []
        if PRICING_JSON_PATH.exists():
            try:
                old_doc = json.loads(PRICING_JSON_PATH.read_text())
                old_rows = old_doc.get("pricing", [])
            except json.JSONDecodeError:
                old_rows = []

        seen = set()
        clean = []
        for r in rows:
            key = (r["provider_id"], r["model_id"])
            if key not in seen:
                seen.add(key)
                clean.append(r)

        pricing_doc = {
            "meta": {
                "last_run_datetime": now_iso_z(),
                "schema_version": "1.1.0",
                "description": "Consolidated pricing for AI models."
            },
            "providers": [
                {
                    "provider_id": "anthropic",
                    "name": "Anthropic",
                    "pricing_source": CLAUDE_URL
                },
                {
                    "provider_id": "openai",
                    "name": "OpenAI",
                    "pricing_source": OPENAI_URL
                },
                {
                    "provider_id": "google",
                    "name": "Google",
                    "pricing_source": VERTEX_URL
                }
            ],
            "pricing": clean 
        }

        PRICING_JSON_PATH.write_text(json.dumps(pricing_doc, indent=2))
        
        send_slack(
            f"Pricing scrape SUCCESS\n"
            f"Rows Saved: {len(clean)}\n"
            f"Time: {now_iso_z()}"
        )

        changes = detect_changes(old_rows, clean)
        if changes:
            message = "🚨 PRICING UPDATE DETECTED \n\n"
            message += format_changes(changes)
            send_slack(message)
        else:
            send_slack("✅ No pricing changes detected.")

    except Exception as e:
        status = "failed"
        error = str(e)
        print(f"CRITICAL ERROR: {error}") 
        send_slack(
            f"Pricing scrape FAILED\n"
            f"Error: {error}\n"
            f"Time: {now_iso_z()}"
        )

    report = {
        "run_id": run_id,
        "started_at": start,
        "finished_at": now_iso_z(),
        "status": status,
        "error": error
    }
    RUN_REPORT_PATH.write_text(json.dumps(report, indent=2))

    print(json.dumps({"event": f"pricing_update_{status}", "run_id": run_id, "error": error}))

if __name__ == "__main__":
    sys.exit(main())