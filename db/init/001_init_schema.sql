CREATE SCHEMA IF NOT EXISTS raw;

-- Pricing JSON
CREATE TABLE IF NOT EXISTS raw.pricing_json (
  content_sha256 TEXT PRIMARY KEY,                     -- hash identifier
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),   -- ingestion time
  payload JSONB NOT NULL                           -- data in json format
);

-- Donors CSV
CREATE TABLE IF NOT EXISTS raw.donor_csv (
    content_sha256 TEXT PRIMARY KEY,                 -- hash identifier
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),   -- ingestion time
    payload JSONB NOT NULL                            -- csv data parsed as json
);