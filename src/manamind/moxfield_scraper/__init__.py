"""Scraper Moxfield -> base SQL.

Zone autonome : ce package scrape moxfield.com et écrit dans ses propres tables
`mox_decks` / `mox_deck_cards` / `mox_commanders`. Il ne touche à aucune table
ManaMind existante. Voir README.md pour les pistes d'intégration.

    from manamind.moxfield_scraper import init_schema, make_engine, scrape

    engine = make_engine(os.environ["DATABASE_URL"])
    init_schema(engine)
    scrape(engine, commander="The Ur-Dragon", limit=500)

Les briques sont utilisables séparément : `parse_deck_html` est une fonction
pure, testable sans réseau ni base.
"""

from .db import init_schema, known_deck_ids, make_engine, upsert_decks
from .models import Card, Deck
from .parser import parse_deck_html
from .pipeline import ScrapeStats, reparse_cache, scrape

__all__ = [
    "Card",
    "Deck",
    "ScrapeStats",
    "init_schema",
    "known_deck_ids",
    "make_engine",
    "parse_deck_html",
    "reparse_cache",
    "scrape",
    "upsert_decks",
]
