from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from manamind.moxfield_scraper import db
from manamind.moxfield_scraper.models import Card, Deck


@pytest.fixture
def engine(tmp_path):
    eng = db.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.init_schema(eng)
    return eng


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


def test_insert_puis_relecture(engine):
    assert db.upsert_decks(engine, [_deck()]) == 1

    with engine.connect() as conn:
        row = conn.execute(select(db.decks)).one()
        cards = conn.execute(select(db.deck_cards.c.card_name)).scalars().all()

    assert row.commander == "The Ur-Dragon"
    assert row.bracket == 4
    assert sorted(cards) == ["Sol Ring", "The Ur-Dragon"]


def test_upsert_remplace_la_decklist(engine):
    db.upsert_decks(engine, [_deck()])

    # Le deck a été modifié sur Moxfield : Sol Ring retiré, Mana Crypt ajouté.
    updated = _deck(cards=[Card("The Ur-Dragon", 1, True), Card("Mana Crypt", 1, False)])
    db.upsert_decks(engine, [updated])

    with engine.connect() as conn:
        cards = conn.execute(select(db.deck_cards.c.card_name)).scalars().all()
        count = conn.execute(select(db.decks.c.deck_id)).scalars().all()

    assert sorted(cards) == ["Mana Crypt", "The Ur-Dragon"]  # pas d'union avec l'ancienne
    assert count == ["abc"]  # pas de doublon de deck


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
