import os
import subprocess
import sys
import argparse
from pathlib import Path
from scripts.ingest_pricing import ingest_pricing
from scripts.ingest_usage import ingest_usage  # placeholder for future usage data

ROOT = Path(__file__).resolve().parent   # the-colonial/
DBT_DIR = ROOT / "dbt"
DBT_BIN = ROOT / "venv" / "bin" / "dbt"


def run(cmd, cwd=None, env=None):
    cmd_str = [str(x) for x in cmd]
    print(f"\n▶ Running: {' '.join(cmd_str)}")
    result = subprocess.run(
        cmd_str,
        cwd=str(cwd) if cwd else None,
        env={**os.environ, **(env or {})},
    )
    if result.returncode != 0:
        sys.exit(result.returncode)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--neon",
        action="store_true",
        help="Insert into Neon DB (default: local Docker Postgres)",
    )
    args = parser.parse_args()
    local = not args.neon  # True = Docker Postgres, False = Neon

    run([sys.executable, "scripts/scrape_pricing.py"], cwd=ROOT)
    ingest_pricing(local=local)
    ingest_usage(local=local)  # no-op until usage script is implemented

    # dbt runs against the same target as ingestion
    dbt_target = "local" if local else "dev"
    run([DBT_BIN, "run", "--select", "staging", "--target", dbt_target], cwd=DBT_DIR)

    print("\nPipeline complete. Check ur DB!")