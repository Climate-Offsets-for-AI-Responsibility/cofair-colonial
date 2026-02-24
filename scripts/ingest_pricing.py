import os
import json
import hashlib
from pathlib import Path

import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]  # data-pipeline/
load_dotenv(ROOT / ".env")

PRICING_PATH = ROOT / "pricing.json"

raw_bytes = PRICING_PATH.read_bytes()
payload = json.loads(raw_bytes)
content_hash = hashlib.sha256(raw_bytes).hexdigest()

conn = psycopg2.connect(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=int(os.getenv("POSTGRES_PORT", "5432")),
    dbname=os.getenv("POSTGRES_DB", "cofair_db"),
    user=os.getenv("POSTGRES_USER", "postgres"),
    password=os.getenv("POSTGRES_PASSWORD", "postgres"),
)

cur = conn.cursor()
cur.execute(
    """
    INSERT INTO raw.pricing_json (content_sha256, payload)
    VALUES (%s, %s)
    ON CONFLICT (content_sha256) DO NOTHING;
    """,
    (content_hash, Json(payload)),
)

conn.commit()
cur.close()
conn.close()

print("Data ingested! hashbrown:", content_hash)