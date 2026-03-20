#!/usr/bin/env python3
"""
Entry point for scheduled pricing scrapes (GitHub Actions).
Delegates to scripts/scrape_pricing.py - run from project root.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from scripts.scrape_pricing import main

if __name__ == "__main__":
    sys.exit(main())
