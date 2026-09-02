"""Scraper Moxfield -> base SQL.

Écrit directement dans les tables centrales de ManaMind :
  - deck_cards  (Bronze layer, géré par Alembic)
  - commanders  (Bronze layer, géré par Alembic)

    from manamind.moxfield_scraper import make_engine, scrape

    engine = make_engine(os.environ["DATABASE_URL"])
    scrape(engine, commander="The Ur-Dragon", limit=500)

Les briques sont utilisables séparément : `parse_deck_html` est une fonction
pure, testable sans réseau ni base.
"""

from .db import known_deck_ids, make_engine, upsert_decks
from .models import Card, Deck
from .parser import parse_deck_html
from .pipeline import ScrapeStats, reparse_cache, scrape

__all__ = [
    "Card",
    "Deck",
    "ScrapeStats",
    "known_deck_ids",
    "make_engine",
    "parse_deck_html",
    "reparse_cache",
    "scrape",
    "upsert_decks",
]
