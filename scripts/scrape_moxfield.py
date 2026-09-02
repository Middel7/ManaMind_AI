#!/usr/bin/env python3
"""
scrape_moxfield.py

Scrape Moxfield et alimente deck_cards + commanders (tables Alembic).

Usage :
    python scripts/scrape_moxfield.py import-commanders data/TOPCOMMANDER.csv
    python scripts/scrape_moxfield.py top --limit 200
    python scripts/scrape_moxfield.py commander "The Ur-Dragon" --limit 500
    python scripts/scrape_moxfield.py recent --limit 1000
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from manamind.moxfield_scraper.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
