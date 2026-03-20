# Data Pipeline (the-colonial)

You can clone this repo into a local folder by running:
```bash
git clone https://github.com/Climate-Offsets-for-AI-Responsibility/the-colonial.git
```

This repo contains scripts and documentation for the Postgres database setup and ingestion.

If anything is unclear or not working correctly feel free to ask me (Andrew).

# Setup

*IMPORTANT* - Always run commands from the `the-colonial` directory!

Create a `.env` file in the root directory (`the-colonial`). See `.env.example` for an example of what yours should look like, the info is also on trello!

Run the following code in your terminal to set up a virtual environment and install dependencies:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

If you don't have it, install Docker Desktop here: [Docker Desktop](https://www.docker.com/products/docker-desktop/)

Run this code to confirm it's installation:
```bash
docker --version
```
If you see a Docker version pop up, Docker is successfully installed.

## Docker Setup

- Open the Docker Desktop application on your computer or run `open -a Docker` in your terminal.
- Run `docker-compose up -d` to tell Docker to build its containers.
- Run `docker ps` to make sure its up and running. (You should see a table of containers)

TLDR: Every time you start up Docker, copy, paste, and run this code from the `the-colonial` directory:
```bash
open -a Docker
docker-compose up -d
docker ps  # Check if everything looks normal
```

## Full Restart (If needed)

If you hit container conflicts or want to start fresh:

```bash
docker-compose down
open -a Docker
# Wait for Docker Desktop to finish starting, then:
docker-compose up -d
docker ps
```

For a full Python env reset too:
```bash
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

&nbsp;

# Running the Insertion Scripts

This section includes the `scrape_pricing` and usage ingestion scripts used to generate data, and then inserts that data into a Postgres DB.

Before running, make sure the docker containers are running and all dependencies are installed.

Just run the following script from the `the-colonial` directory:
```bash
python3 build_db.py
```

After this runs, all of the data should be inserted into the local Docker Postgres DB. To insert into Neon instead (not currently avaliable), use:
```bash
python3 build_db.py --neon
```
This requires `NETLIFY_DATABASE_URL_UNPOOLED` in `.env`.

The pipeline will:
- Run `scrape_pricing.py` to fetch latest pricing from Claude, OpenAI, and Vertex
- Ingest pricing into `raw.pricing_json`
- Ingest usage (dataclaw) from HuggingFace into `raw.usage`
- Run dbt to build `staging.stg_pricing` and `staging.stg_usage`

## Scheduled Pricing Updates

The root `scrape_pricing.py` is used by GitHub Actions for daily runs. Run manually:
```bash
python3 scrape_pricing.py
```

&nbsp;

# Inspecting the Database

### Postgres Setup

After the data has been inserted, you can check if everything worked correctly by looking inside the database.

First execute this command to enter the Postgres container:
```bash
docker-compose exec postgres bash
# enter exit to quit
```

You are now inside the Docker container for Postgres. From here you can run the following from inside the container to start Postgres:
```bash
psql -U postgres
# enter \q to quit
```

You should see a prompt like `postgres=#`, meaning you can now enter postgres commands to navigate the database.

Some useful commands:
- `\l` - lists all databases (We'll use `cofair_db`)
- `\c cofair_db` - connects to a database
- `\dn` - lists all schemas in a database
- `\dt` - lists all tables in a database
- `\dt raw.*` - tables in raw schema
- `\dv staging.*` - views in staging schema
- SQL commands like `SELECT * FROM staging.stg_pricing LIMIT 10;`

### neon_testing notebook

Use `neon_testing.ipynb` to load pricing and usage data into pandas DataFrames. Connects to local Postgres (ensure Docker is running).

&nbsp;

# Database Architecture

```
cofair_db
├── raw (schema)
│   ├── pricing_json (table)  – scraped pricing data
│   └── usage (table)         – dataclaw usage from HuggingFace
│
├── staging (schema)
│   ├── stg_pricing (view)    – parsed pricing by provider/model/sku
│   └── stg_usage (view)      – parsed usage: session_id, model, tokens, etc.
```

## Staging Schema Reference

### staging.stg_pricing

| Column                  | Type        |
|-------------------------|-------------|
| content_sha256          | text        |
| ingested_at             | timestamptz |
| pricing_version         | text        |
| currency                | text        |
| provider                | text        |
| model                   | text        |
| sku_type                | text        |
| price_per_1m_tokens_usd | numeric     |
| price_per_1k_tokens_usd | numeric     |

### staging.stg_usage

| Column        | Type      |
|---------------|-----------|
| dataset_id    | text      |
| session_id    | text      |
| model         | text      |
| git_branch    | text      |
| start_time    | timestamp |
| end_time      | timestamp |
| project       | text      |
| messages      | jsonb     |
| input_tokens  | bigint    |
| output_tokens | bigint    |
