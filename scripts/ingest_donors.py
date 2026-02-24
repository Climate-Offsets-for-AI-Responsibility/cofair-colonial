import os
import csv
import hashlib
from pathlib import Path

import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv

# Load .env
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# donors.csv is in same directory
DONORS_PATH = Path(__file__).resolve().parents[1] / "donors.csv"

# Read + hash
raw_bytes = DONORS_PATH.read_bytes()
content_hash = hashlib.sha256(raw_bytes).hexdigest()

# Parse CSV
with DONORS_PATH.open(newline="", encoding="utf-8") as f:
    payload = list(csv.DictReader(f))

# Connect
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
    INSERT INTO raw.donor_csv (content_sha256, payload)
    VALUES (%s, %s)
    ON CONFLICT (content_sha256) DO NOTHING;
    """,
    (content_hash, Json(payload)),
)

conn.commit()
cur.close()
conn.close()

print("Data ingested! hashbrown:", content_hash)