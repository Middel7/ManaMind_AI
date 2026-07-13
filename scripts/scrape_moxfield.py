#!/usr/bin/env python3
"""
scrape_moxfield.py

Scrape Moxfield et alimente les tables mox_decks / mox_deck_cards / mox_commanders.
Remplace la chaîne « Mox scrapper -> CSV sur le Bureau -> import_deck_cards.py » :
le HTML transite en mémoire, plus aucun fichier intermédiaire.

Usage :
    python scripts/scrape_moxfield.py init-db
    python scripts/scrape_moxfield.py import-commanders data/TOPCOMMANDER.csv
    python scripts/scrape_moxfield.py top --limit 200
    python scripts/scrape_moxfield.py commander "The Ur-Dragon" --limit 500
    python scripts/scrape_moxfield.py recent --limit 1000

Le premier run mérite --no-headless : on voit le navigateur travailler, et si
Moxfield a changé son markup ça se voit tout de suite au lieu de récolter une
base vide.
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
