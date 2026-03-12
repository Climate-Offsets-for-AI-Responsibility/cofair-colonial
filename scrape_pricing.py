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





def http_get(url, retries=3):

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)

        try:

            context = browser.new_context(

                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

            )

            page = context.new_page()

            print(f"FETCHING via Playwright: {url}")

            

            for i in range(retries):

                try:

                    page.goto(url, wait_until="networkidle", timeout=60000)

                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

                    page.wait_for_timeout(2000) 

                    return page.content()

                except Exception as e:

                    if i == retries - 1:

                        raise RuntimeError(f"Failed after {retries} attempts: {e}")

                    time.sleep(5)

        finally:

            browser.close() # This now runs even if an error is raised



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

        col_input = next(i for i, h in enumerate(headers) if h == "input" or h == "base input")

        col_output = next(i for i, h in enumerate(headers) if h == "output" or h == "base output")



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



            # --- FIX: ROBUST COLUMN DETECTION ---

            input_price = None

            output_price = None



            # Iterate through the row using headers to find the right index

            for idx, h in enumerate(headers):

                if idx >= len(r): 

                    continue

                

                cell_val = money(r[idx])

                if cell_val is None: 

                    continue



                if "input" in h:

                    input_price = cell_val

                elif "output" in h:

                    output_price = cell_val



            # Fallback: if no clear headers, try to grab the first number found

            if input_price is None and output_price is None:

                prices = [money(x) for x in r if money(x) is not None]

                if not prices:

                    continue

                output_price = prices[0]

            # ------------------------------------



            label = norm(r[0] if len(r) == 1 else r[1])

            label_id = model_id_from_name(label)



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



        col_input = next((i for i, h in enumerate(headers) if h == "input"), None)

        col_cached = next((i for i, h in enumerate(headers) if "cached" in h), None)

        col_output = next((i for i, h in enumerate(headers) if h == "output"), None)

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

            lines.append(f"• {r['display_name']} (In: ${r['input_price']}, Out: ${r['output_price']})")



        elif c[0] == "PRICE_CHANGED":

            new_r, old_r = c[1], c[2]

            lines.append(

                f"• *{new_r['display_name']}*\n"

                f"  Input: ${old_r['input_price']} → ${new_r['input_price']}\n"

                f"  Output: ${old_r['output_price']} → ${new_r['output_price']}"

            )



        elif c[0] == "REMOVED_MODEL":

            r = c[1]

            lines.append(f"• {r['display_name']}")



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

            key = (r["provider_id"], r["model_id"], r["type"])

            if key not in seen:

                seen.add(key)

                clean.append(r)



        # sanity check

        if len(old_rows) > 0 and len(clean) < (len(old_rows) * 0.5):

            raise RuntimeError(

                f"Sanity check failed: Scraped only {len(clean)} models, "

                f"expected roughly {len(old_rows)}. Potential parser failure."

            )



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

            # Categorize the changes

            new_models = [c for c in changes if c[0] == "NEW_MODEL"]

            price_changes = [c for c in changes if c[0] == "PRICE_CHANGED"]

            removed_models = [c for c in changes if c[0] == "REMOVED_MODEL"]



            message_blocks = []



            if price_changes:

                message_blocks.append("💰 *PRICING CHANGES DETECTED*")

                message_blocks.append(format_changes(price_changes))

            

            if new_models:

                # Add a separator if there was a previous block

                if message_blocks: message_blocks.append("---")

                message_blocks.append("✨ *NEW MODELS DETECTED*")

                message_blocks.append(format_changes(new_models))

            

            if removed_models:

                if message_blocks: message_blocks.append("---")

                message_blocks.append("❌ *MODELS REMOVED*")

                message_blocks.append(format_changes(removed_models))



            # Join everything into one message

            full_message = "\n\n".join(message_blocks)

            send_slack(full_message)

        else:

            send_slack("✅ No changes detected (Pricing and Model list stable).")



        # Log and return success

        report = {

            "run_id": run_id,

            "started_at": start,

            "finished_at": now_iso_z(),

            "status": "success",

            "error": None

        }

        RUN_REPORT_PATH.write_text(json.dumps(report, indent=2))

        print(json.dumps({"event": "pricing_update_success", "run_id": run_id, "error": None}))

        return 0



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

            "status": "failed",

            "error": error

        }

        RUN_REPORT_PATH.write_text(json.dumps(report, indent=2))

        print(json.dumps({"event": "pricing_update_failed", "run_id": run_id, "error": error}))

        return 1