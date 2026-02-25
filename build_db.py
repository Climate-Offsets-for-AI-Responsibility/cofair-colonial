import subprocess
import sys
import argparse
from pathlib import Path
from scripts.ingest_donors import ingest_donors
from scripts.ingest_pricing import ingest_pricing

ROOT = Path(__file__).resolve().parent   # the-colonial/
DBT_DIR = ROOT / "dbt"
DBT_BIN = ROOT / "venv" / "bin" / "dbt"


def run(cmd, cwd=None):
    cmd_str = [str(x) for x in cmd]
    print(f"\n▶ Running: {' '.join(cmd_str)}")
    result = subprocess.run(cmd_str, cwd=str(cwd) if cwd else None)
    if result.returncode != 0:
        sys.exit(result.returncode)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run pipeline against local Docker Postgres (default: Neon)"
    )
    args = parser.parse_args()
    local = args.local  # True = Docker, False = Neon

    run([sys.executable, "scripts/scrape_pricing.py"], cwd=ROOT)
    ingest_pricing(local=local)
    ingest_donors(local=local)
    run([DBT_BIN, "run", "--select", "staging"], cwd=DBT_DIR)

    print("\nPipeline complete. Check ur DB!")