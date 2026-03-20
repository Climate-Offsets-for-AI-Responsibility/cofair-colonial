"""
Ingest usage data (dataclaw from HuggingFace) into raw.usage.
Fetches datasets from HuggingFace and inserts each row into Postgres.
"""
import os
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
            raise RuntimeError(
                "Set NETLIFY_DATABASE_URL_UNPOOLED in your .env! Or ask Andrew for it!"
            )
        return psycopg2.connect(db_url)

    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5434")),
        dbname=os.getenv("POSTGRES_DB", "cofair_db"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )


def ingest_usage(local: bool = True):
    """
    Fetch dataclaw datasets from HuggingFace and insert into raw.usage.
    """
    from huggingface_hub import HfApi
    from datasets import load_dataset

    api = HfApi()
    dataclaw_datasets = list(api.list_datasets(search="dataclaw"))
    repo_ids = [d.id for d in dataclaw_datasets]

    print(f"Found {len(repo_ids)} usage datasets: {repo_ids}")

    conn = get_connection(local=local)
    try:
        # Ensure table exists (for existing DBs that predate the schema)
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS raw;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS raw.usage (
                    id SERIAL PRIMARY KEY,
                    dataset_id TEXT NOT NULL,
                    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    payload JSONB NOT NULL
                );
            """)
        conn.commit()

        for d_set in dataclaw_datasets:
            d_id = d_set.id
            try:
                ds = load_dataset(d_id, split="train", streaming=True)
                count = 0
                with conn.cursor() as cur:
                    for row in ds:
                        cur.execute(
                            """
                            INSERT INTO raw.usage (dataset_id, payload)
                            VALUES (%s, %s);
                            """,
                            (d_id, Json(dict(row))),
                        )
                        count += 1
                conn.commit()
                print(f"{d_id} finished ({count} rows)")
            except Exception as e:
                conn.rollback()
                print(f"{d_id} skipped: {e}")
    finally:
        conn.close()

    print("Usage data ingested!")


if __name__ == "__main__":
    ingest_usage(local=True)
