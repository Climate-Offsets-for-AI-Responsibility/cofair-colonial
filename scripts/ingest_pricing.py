import os
import json
import hashlib
import uuid
import argparse
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]  # the-colonial/
load_dotenv(ROOT / ".env")
RUN_REPORT_PATH = ROOT / "run_report.json"


def get_connection(local: bool = True):
    """
    local=True  -> inserts into Docker/Postgres
    local=False -> inserts into Netlify/Neon
    """
    if not local:
        db_url = os.getenv("NETLIFY_DATABASE_URL_UNPOOLED")
        if not db_url:
            raise RuntimeError("Set NETLIFY_DATABASE_URL_UNPOOLED in your .env! Or ask Andrew for it!")
        return psycopg2.connect(db_url)

    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5434")),
        dbname=os.getenv("POSTGRES_DB", "cofair_db"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )


def ensure_pricing_tables(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS raw;")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS raw.pricing_json (
                content_sha256 TEXT PRIMARY KEY,
                ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                payload JSONB NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS raw.pricing_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                run_id TEXT,
                content_sha256 TEXT NOT NULL,
                scraped_at TIMESTAMPTZ NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                payload JSONB NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pricing_snapshots_scraped_at
            ON raw.pricing_snapshots (scraped_at DESC);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pricing_snapshots_content_sha256
            ON raw.pricing_snapshots (content_sha256);
            """
        )
    conn.commit()


def parse_scraped_at(payload):
    value = payload.get("meta", {}).get("last_run_datetime")
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_run_id():
    if not RUN_REPORT_PATH.exists():
        return None
    try:
        report = json.loads(RUN_REPORT_PATH.read_text())
    except json.JSONDecodeError:
        return None
    return report.get("run_id")


def ingest_pricing(local: bool = True):
    pricing_path = ROOT / "pricing.json"
    raw_bytes = pricing_path.read_bytes()
    payload = json.loads(raw_bytes)
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    snapshot_id = str(uuid.uuid4())
    run_id = load_run_id()
    scraped_at = parse_scraped_at(payload)

    conn = get_connection(local=local)
    try:
        ensure_pricing_tables(conn)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw.pricing_snapshots (
                    snapshot_id,
                    run_id,
                    content_sha256,
                    scraped_at,
                    payload
                )
                VALUES (%s, %s, %s, %s, %s);
                """,
                (snapshot_id, run_id, content_hash, scraped_at, Json(payload)),
            )
            cur.execute(
                """
                INSERT INTO raw.pricing_json (content_sha256, payload)
                VALUES (%s, %s)
                ON CONFLICT (content_sha256) DO NOTHING;
                """,
                (content_hash, Json(payload)),
            )
        conn.commit()
    finally:
        conn.close()

    print("Pricing snapshot ingested!", snapshot_id, content_hash)
    return {
        "snapshot_id": snapshot_id,
        "content_sha256": content_hash,
        "run_id": run_id,
        "scraped_at": scraped_at.isoformat(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--neon",
        action="store_true",
        help="Insert pricing into Neon instead of local Docker Postgres",
    )
    args = parser.parse_args()
    ingest_pricing(local=not args.neon)
