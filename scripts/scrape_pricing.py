import json
import os
import re
import smtplib
import sys
import time
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


CLAUDE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
VERTEX_URL = "https://cloud.google.com/vertex-ai/generative-ai/pricing"

ROOT = Path(__file__).resolve().parents[1]  # data-pipeline/
PRICING_JSON_PATH = ROOT / "pricing.json"
RUN_REPORT_PATH = ROOT / "run_report.json"
LAST_RUN_PATH = ROOT / ".last_pricing_scrape.json"

MAX_RUNS_PER_DAY = 1

REQUIRED_ROW_FIELDS = {"provider", "model", "sku_type", "source"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def utc_day():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def jlog(event, **fields):
    payload = {"ts": now_iso(), "event": event}
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def load_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def capacity_check():
    meta = load_json(LAST_RUN_PATH) or {}
    day = utc_day()
    runs_today = int((meta.get("runs_by_day") or {}).get(day, 0))
    if runs_today >= MAX_RUNS_PER_DAY:
        return False, {"reason": "capacity_limit", "day": day, "runs_today": runs_today, "max_runs_per_day": MAX_RUNS_PER_DAY}
    return True, {"reason": "ok", "day": day, "runs_today": runs_today, "max_runs_per_day": MAX_RUNS_PER_DAY}


def capacity_record_run():
    meta = load_json(LAST_RUN_PATH) or {}
    day = utc_day()
    runs_by_day = meta.get("runs_by_day") or {}
    runs_by_day[day] = int(runs_by_day.get(day, 0)) + 1
    meta["runs_by_day"] = runs_by_day
    meta["last_run_utc"] = now_iso()
    save_json(LAST_RUN_PATH, meta)


def send_failure_email(subject, body):
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587").strip() or "587")
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_PASS", "").strip()
    email_to = os.environ.get("EMAIL_TO", "").strip()
    email_from = os.environ.get("EMAIL_FROM", smtp_user).strip()

    result = {"attempted": True, "sent": False, "error": None}
    if not (smtp_host and smtp_user and smtp_pass and email_to and email_from):
        result["error"] = "Missing env vars: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_TO, EMAIL_FROM"
        return result

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=25) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        result["sent"] = True
        return result
    except Exception as e:
        result["error"] = str(e)
        return result


def make_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)


def render_html(url, wait_xpath, timeout_s=30):
    driver = make_driver()
    try:
        driver.get(url)

        WebDriverWait(driver, timeout_s).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        if url == VERTEX_URL:
            last_height = driver.execute_script("return document.body.scrollHeight")
            for _ in range(6):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height

        WebDriverWait(driver, timeout_s).until(
            EC.presence_of_element_located((By.XPATH, wait_xpath))
        )

        return driver.page_source

    finally:
        try:
            driver.quit()
        except Exception:
            pass



def parse_money_per_mtok(cell_text):
    t = " ".join(cell_text.split())
    m = re.search(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*MTok", t, flags=re.IGNORECASE)
    if not m:
        m = re.search(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*per\s*MTok", t, flags=re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1))


def norm_model(s):
    x = " ".join(str(s).strip().split())
    x = x.replace("—", "-").replace("–", "-")
    x = x.lower()
    x = re.sub(r"\s+", "-", x)
    x = re.sub(r"[^a-z0-9\.\-_]+", "-", x)
    x = re.sub(r"-{2,}", "-", x).strip("-")
    return x


def table_matrix(table):
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        row = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
        if any(x for x in row):
            rows.append(row)
    return rows


def dedupe_rows(rows):
    seen = set()
    out = []
    for r in rows:
        key = (r.get("provider"), r.get("model"), r.get("sku_type"), r.get("tier", ""), r.get("price_per_1m_tokens_usd"), r.get("source"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def claude_from_tables(html):
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []
    header_map = {
        "base input": "base_input_tokens",
        "base input tokens": "base_input_tokens",
        "input": "base_input_tokens",
        "output": "output_tokens",
        "output tokens": "output_tokens",
        "cache writes (5m)": "cache_write_5m",
        "cache write (5m)": "cache_write_5m",
        "cache write 5m": "cache_write_5m",
        "cache writes (1h)": "cache_write_1h",
        "cache write (1h)": "cache_write_1h",
        "cache write 1h": "cache_write_1h",
        "cache hits & refresh": "cache_hit_refresh",
        "cache hits and refresh": "cache_hit_refresh",
        "cache hit & refresh": "cache_hit_refresh",
        "cache hits": "cache_hit_refresh",
        "cache hit": "cache_hit_refresh",
        "refresh": "cache_hit_refresh",
    }

    for table in soup.find_all("table"):
        mat = table_matrix(table)
        if len(mat) < 2:
            continue
        headers = [h.lower() for h in mat[0]]
        if not any("mtok" in " ".join(r).lower() or "$" in " ".join(r) for r in mat[1:]):
            continue

        col_sku = {}
        for j, h in enumerate(headers):
            hl = h.lower()
            for k, sku in header_map.items():
                if k in hl:
                    col_sku[j] = sku
                    break

        if not col_sku:
            continue

        model_col = 0
        for i in range(1, len(mat)):
            row = mat[i]
            if len(row) < 2:
                continue
            model_raw = row[model_col]
            if not model_raw or "claude" not in model_raw.lower():
                if "opus" in model_raw.lower() or "sonnet" in model_raw.lower() or "haiku" in model_raw.lower():
                    pass
                else:
                    continue
            model = norm_model(model_raw.replace("Claude", "claude").replace(" ", "-"))
            for j, sku in col_sku.items():
                if j >= len(row):
                    continue
                price = parse_money_per_mtok(row[j])
                if price is None:
                    continue
                rows_out.append(
                    {
                        "provider": "anthropic",
                        "model": model,
                        "sku_type": sku,
                        "price_per_1m_tokens_usd": price,
                        "price_per_1k_tokens_usd": round(price / 1000.0, 10),
                        "source": CLAUDE_URL,
                    }
                )

    if rows_out:
        return dedupe_rows(rows_out)

    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    current_model = None
    for ln in lines:
        if "claude" in ln.lower() and any(x in ln.lower() for x in ["opus", "sonnet", "haiku"]):
            current_model = norm_model(ln)
        price = parse_money_per_mtok(ln)
        if current_model and price is not None:
            low = ln.lower()
            sku = None
            if "base input" in low:
                sku = "base_input_tokens"
            elif "cache write" in low and ("5m" in low or "5 min" in low):
                sku = "cache_write_5m"
            elif "cache write" in low and ("1h" in low or "1 hour" in low):
                sku = "cache_write_1h"
            elif "cache hit" in low or "refresh" in low:
                sku = "cache_hit_refresh"
            elif "output" in low:
                sku = "output_tokens"
            if sku:
                rows_out.append(
                    {
                        "provider": "anthropic",
                        "model": current_model,
                        "sku_type": sku,
                        "price_per_1m_tokens_usd": price,
                        "price_per_1k_tokens_usd": round(price / 1000.0, 10),
                        "source": CLAUDE_URL,
                    }
                )
    return dedupe_rows(rows_out)


def vertex_from_tables(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    for table in soup.find_all("table"):
        mat = table_matrix(table)

        if len(mat) < 2:
            continue

        # Check if table contains Gemini models
        table_text = " ".join(" ".join(r) for r in mat).lower()
        if "gemini" not in table_text:
            continue

        headers = [h.lower() for h in mat[0]]

        model_col = 0  # first column = model name

        for r in mat[1:]:
            if model_col >= len(r):
                continue

            model_raw = r[model_col].lower()
            if "gemini" not in model_raw:
                continue

            model = re.sub(r"\s+", "-", model_raw)
            model = re.sub(r"[^a-z0-9\.\-]+", "-", model).strip("-")

            # Scan entire row for dollar values
            for cell in r:
                m = re.search(r"\$\s*([0-9]+(?:\.[0-9]+)?)", cell)
                if not m:
                    continue

                price = float(m.group(1))

                rows.append({
                    "provider": "google",
                    "model": model,
                    "sku_type": "input_tokens",
                    "price_per_1m_tokens_usd": price,
                    "price_per_1k_tokens_usd": price / 1000,
                    "source": VERTEX_URL,
                })

    return rows




def validate_rows(rows):
    errors = []
    if not rows:
        return ["No rows produced."]

    providers = set()
    for i, r in enumerate(rows):
        missing = [k for k in REQUIRED_ROW_FIELDS if k not in r or r.get(k) in (None, "")]
        if missing:
            errors.append(f"Row {i} missing fields: {missing}")
        providers.add(r.get("provider"))
        p = r.get("price_per_1m_tokens_usd", None)
        if not isinstance(p, (int, float)):
            errors.append(f"Row {i} price_per_1m_tokens_usd not numeric")
        else:
            if p < 0:
                errors.append(f"Row {i} negative price")
    if "anthropic" not in providers:
        errors.append("No Anthropic rows produced.")
    if "google" not in providers:
        errors.append("No Vertex/Google rows produced.")
    return errors


def main():
    run_id = str(uuid.uuid4())
    started_at = now_iso()
    jlog("pricing_scrape_start", run_id=run_id)

    ok_run, cap = capacity_check()
    if not ok_run:
        report = {"run_id": run_id, "started_at": started_at, "finished_at": now_iso(), "status": "skipped", "capacity": cap}
        save_json(RUN_REPORT_PATH, report)
        jlog("pricing_scrape_skipped", run_id=run_id, capacity=cap)
        return 0

    status = "success"
    rows = []
    sources_fetched = []
    validation_errors = []
    notification = {"attempted": False}

    try:
        t0 = time.time()
        claude_html = render_html(CLAUDE_URL, "//*[contains(text(),'MTok')]")
        sources_fetched.append({"provider": "anthropic", "source": CLAUDE_URL, "status": "rendered", "bytes_read": len(claude_html)})
        claude_rows = claude_from_tables(claude_html)
        jlog("provider_parsed", run_id=run_id, provider="anthropic", rows=len(claude_rows), seconds=round(time.time() - t0, 3))
        rows.extend(claude_rows)

        t1 = time.time()
        vertex_html = render_html(VERTEX_URL, "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'gemini')]")
        sources_fetched.append({"provider": "google", "source": VERTEX_URL, "status": "rendered", "bytes_read": len(vertex_html)})
        vertex_rows = vertex_from_tables(vertex_html)
        jlog("provider_parsed", run_id=run_id, provider="google", rows=len(vertex_rows), seconds=round(time.time() - t1, 3))
        rows.extend(vertex_rows)

        rows = dedupe_rows(rows)
        validation_errors = validate_rows(rows)
        if validation_errors:
            raise RuntimeError("Validation failed: " + " | ".join(validation_errors))

        out = {"version": "v1-table", "currency": "USD", "rows": rows}
        save_json(PRICING_JSON_PATH, out)

        capacity_record_run()

        rows_by_provider = {}
        for r in rows:
            rows_by_provider[r["provider"]] = rows_by_provider.get(r["provider"], 0) + 1
        jlog("pricing_scrape_success", run_id=run_id, rows_written_total=len(rows), rows_by_provider=rows_by_provider)

    except Exception as e:
        status = "failed"
        err = str(e)
        jlog("pricing_scrape_failed", run_id=run_id, error=err)
        subject = "Pricing scrape FAILED"
        body = "\n".join(
            [
                f"run_id: {run_id}",
                f"started_at: {started_at}",
                f"finished_at: {now_iso()}",
                f"error: {err}",
                f"claude_url: {CLAUDE_URL}",
                f"vertex_url: {VERTEX_URL}",
            ]
        )
        # notification = send_failure_email(subject, body)
        jlog("failure_email_result", run_id=run_id, result=notification)

    finished_at = now_iso()
    rows_by_provider = {}
    for r in rows:
        rows_by_provider[r.get("provider")] = rows_by_provider.get(r.get("provider"), 0) + 1

    report = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "capacity": cap,
        "sources_fetched": sources_fetched,
        "rows_written_total": len(rows) if status == "success" else 0,
        "rows_by_provider": rows_by_provider,
        "validation_errors": validation_errors,
        "notification": notification,
    }
    save_json(RUN_REPORT_PATH, report)

    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
