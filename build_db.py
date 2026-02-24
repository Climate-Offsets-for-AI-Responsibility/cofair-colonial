import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent   # data-pipeline/
DBT_DIR = ROOT / "dbt"
DBT_BIN = ROOT / "venv" / "bin" / "dbt"

def run(cmd, cwd=None):
    cmd_str = [str(x) for x in cmd]
    print(f"\n▶ Running: {' '.join(cmd_str)}")
    result = subprocess.run(cmd_str, cwd=str(cwd) if cwd else None)
    if result.returncode != 0:
        sys.exit(result.returncode)

if __name__ == "__main__":
    run([sys.executable, "scripts/scrape_pricing.py"], cwd=ROOT)
    run([sys.executable, "scripts/ingest_pricing.py"], cwd=ROOT)
    run([sys.executable, "scripts/ingest_donors.py"], cwd=ROOT)
    run([DBT_BIN, "run", "--select", "staging"], cwd=DBT_DIR)
    print("\nPipeline complete. Check Postgres DB!")