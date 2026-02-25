import os
import csv
import hashlib
from pathlib import Path

import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]  # the-colonial/
load_dotenv(ROOT / ".env")


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
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "cofair_db"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )


def ingest_donors(local: bool = True):
    donors_path = ROOT / "donors.csv"

    raw_bytes = donors_path.read_bytes()
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    with donors_path.open(newline="", encoding="utf-8") as f:
        payload = list(csv.DictReader(f))

    conn = get_connection(local=local)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw.donor_csv (content_sha256, payload)
                VALUES (%s, %s)
                ON CONFLICT (content_sha256) DO NOTHING;
                """,
                (content_hash, Json(payload)),
            )
        conn.commit()
    finally:
        conn.close()

    print("Data ingested! hashbrown:", content_hash)
    return content_hash


if __name__ == "__main__":
    # Default is local Docker DB
    ingest_donors(local=True)