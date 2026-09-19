"""Persistance du scraper Moxfield.

Le module ecrit dans les tables centrales de ManaMind avec du SQL PostgreSQL
(`= ANY(...)`, `ON CONFLICT`) : ces tests demandent donc une vraie base. Ils
travaillent dans un schema jetable, cree puis supprime, pour ne jamais toucher
aux 38 millions de lignes de deck_cards.
"""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import os

import pytest
from sqlalchemy import text

from manamind.moxfield_scraper import db
from manamind.moxfield_scraper.models import Card, Deck

# Le schema jetable, reduit a ce que le module ecrit.
_DDL = """
CREATE TABLE deck_cards (
    id               bigserial PRIMARY KEY,
    deck_id          text,
    commander        text,
    card_name        text,
    is_commander     boolean,
    quantity         smallint,
    bracket          smallint,
    price            numeric,
    currency         varchar,
    deck_type        varchar,
    date_created     timestamptz,
    date_modified    timestamptz,
    first_scraped_at timestamptz,
    scraped_at       timestamptz
);
CREATE TABLE commanders (
    name               varchar PRIMARY KEY,
    rank               integer,
    color_identity     varchar,
    decks_extracted    integer,
    first_extracted_at timestamptz,
    last_scraped_at    timestamptz
);
"""


def _postgres_url() -> str | None:
    """URL PostgreSQL du projet, lue hors de l'environnement de test.

    La suite pose une base SQLite en memoire pour les autres tests : elle ne
    convient pas ici, le module parlant explicitement PostgreSQL.
    """
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgres"):
        return url
    env = Path(__file__).resolve().parents[2] / ".env"
    if not env.exists():
        return None
    for ligne in env.read_text(encoding="utf-8", errors="replace").splitlines():
        cle, _, valeur = ligne.partition("=")
        if cle.strip() == "DATABASE_URL" and valeur.strip().startswith("postgres"):
            return valeur.strip().strip('"').strip("'")
    return None


@pytest.fixture
def engine():
    url = _postgres_url()
    if not url:
        pytest.skip("ces tests demandent PostgreSQL : aucune DATABASE_URL utilisable")

    schema = f"mm_test_{uuid4().hex[:8]}"
    socle = db.make_engine(url)
    with socle.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))

    # Tout passe par le schema jetable : les tables reelles restent intactes.
    eng = db.make_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    with eng.begin() as conn:
        conn.execute(text(_DDL))
    try:
        yield eng
    finally:
        eng.dispose()
        with socle.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        socle.dispose()


def _deck(deck_id="abc", cards=None) -> Deck:
    return Deck(
        deck_id=deck_id,
        commander="The Ur-Dragon",
        deck_type="CEDH",
        date_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
        date_modified=datetime(2025, 1, 1, tzinfo=timezone.utc),
        bracket=4,
        price=Decimal("955.39"),
        currency="$",
        cards=cards
        or [
            Card("The Ur-Dragon", 1, True),
            Card("Sol Ring", 1, False),
        ],
    )


def _cartes(engine, deck_id="abc") -> list[str]:
    with engine.connect() as conn:
        return sorted(conn.execute(
            text("SELECT card_name FROM deck_cards WHERE deck_id = :d"),
            {"d": deck_id},
        ).scalars().all())


def test_insert_puis_relecture(engine):
    assert db.upsert_decks(engine, [_deck()]) == (1, 0)

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT commander, bracket, deck_type, price, currency
            FROM deck_cards WHERE deck_id = 'abc' LIMIT 1
        """)).one()

    assert row.commander == "The Ur-Dragon"
    assert row.bracket == 4
    assert row.deck_type == "CEDH"
    assert _cartes(engine) == ["Sol Ring", "The Ur-Dragon"]


def test_upsert_remplace_la_decklist(engine):
    db.upsert_decks(engine, [_deck()])

    # Le deck a été modifié sur Moxfield : Sol Ring retiré, Mana Crypt ajouté.
    updated = _deck(cards=[Card("The Ur-Dragon", 1, True), Card("Mana Crypt", 1, False)])
    assert db.upsert_decks(engine, [updated]) == (0, 1)

    # Pas d'union avec l'ancienne liste, et un seul deck en base.
    assert _cartes(engine) == ["Mana Crypt", "The Ur-Dragon"]
    with engine.connect() as conn:
        ids = conn.execute(text("SELECT DISTINCT deck_id FROM deck_cards")).scalars().all()
    assert ids == ["abc"]


def test_premiere_visite_conservee(engine):
    """La date de premiere collecte ne bouge pas quand le deck est revu."""
    db.upsert_decks(engine, [_deck()])
    with engine.connect() as conn:
        premiere = conn.execute(text(
            "SELECT MIN(first_scraped_at) FROM deck_cards WHERE deck_id = 'abc'")).scalar()

    db.upsert_decks(engine, [_deck(cards=[Card("Mana Crypt", 1, False)])])
    with engine.connect() as conn:
        apres = conn.execute(text(
            "SELECT MIN(first_scraped_at) FROM deck_cards WHERE deck_id = 'abc'")).scalar()

    assert apres == premiere


def test_known_deck_ids(engine):
    db.upsert_decks(engine, [_deck("aaa"), _deck("bbb")])

    assert db.known_deck_ids(engine, ["aaa", "ccc"]) == {"aaa"}
    assert db.known_deck_ids(engine, []) == set()


def test_etat_des_commandants(engine):
    db.upsert_commanders(engine, [
        {"name": "The Ur-Dragon", "rank": 1, "color_identity": "WUBRG"},
        {"name": "Edgar Markov", "rank": 2, "color_identity": "RWB"},
    ])

    assert db.pending_commanders(engine) == ["The Ur-Dragon", "Edgar Markov"]

    db.mark_commander_scraped(engine, "The Ur-Dragon", 120)

    assert db.pending_commanders(engine) == ["Edgar Markov"]
    assert db.pending_commanders(engine, refresh=True) == ["The Ur-Dragon", "Edgar Markov"]
    assert db.pending_commanders(engine, start_rank=2) == ["Edgar Markov"]
