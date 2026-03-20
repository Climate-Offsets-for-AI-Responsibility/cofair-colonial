CREATE SCHEMA IF NOT EXISTS raw;

-- Pricing JSON (from scrape_pricing.py)
CREATE TABLE IF NOT EXISTS raw.pricing_json (
  content_sha256 TEXT PRIMARY KEY,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL
);

-- Usage (dataclaw from HuggingFace)
CREATE TABLE IF NOT EXISTS raw.usage (
  id SERIAL PRIMARY KEY,
  dataset_id TEXT NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL
);