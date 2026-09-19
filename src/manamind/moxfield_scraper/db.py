"""Persistance SQL du scraper — écrit dans deck_cards et commanders.

Le scraper n'a plus de tables propres : il alimente directement les tables
centrales de ManaMind, gérées par Alembic.

Stratégie d'upsert pour un deck :
  1. Récupérer first_scraped_at existant (pour l'immuabilité).
  2. DELETE toutes les lignes de ce deck_id dans deck_cards.
  3. INSERT les nouvelles cartes avec les métadonnées du deck.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean, Column, MetaData, Numeric, SmallInteger, Table, TIMESTAMP,
    Text, create_engine, insert as sa_insert, text,
)
from sqlalchemy.engine import Engine

from .models import Deck


def make_engine(url: str, **kwargs: object) -> Engine:
    return create_engine(url, future=True, **kwargs)


# Définition minimale pour insertmanyvalues (sans la PK auto-générée `id`).
# SQLAlchemy 2.0 + PostgreSQL batche automatiquement en N/1000 statements
# au lieu de N×1 — gain ×10-50 sur les gros batchs.
_deck_cards_t = Table(
    "deck_cards",
    MetaData(),
    Column("deck_id", Text),
    Column("commander", Text),
    Column("card_name", Text),
    Column("is_commander", Boolean),
    Column("quantity", SmallInteger),
    Column("bracket", Numeric),
    Column("price", Numeric),
    Column("currency", Text),
    Column("deck_type", Text),
    Column("date_created", TIMESTAMP(timezone=True)),
    Column("date_modified", TIMESTAMP(timezone=True)),
    Column("first_scraped_at", TIMESTAMP(timezone=True)),
    Column("scraped_at", TIMESTAMP(timezone=True)),
)

_INSERT_COLS = (
    "deck_id", "commander", "card_name", "is_commander", "quantity",
    "bracket", "price", "currency", "deck_type",
    "date_created", "date_modified", "first_scraped_at", "scraped_at",
)


def _bulk_insert(conn, card_rows: list[tuple]) -> None:
    if not card_rows:
        return
    conn.execute(
        sa_insert(_deck_cards_t),
        [dict(zip(_INSERT_COLS, row)) for row in card_rows],
    )


# ── Upsert décks ──────────────────────────────────────────────────────────────

def upsert_decks(engine: Engine, batch: Iterable[Deck]) -> tuple[int, int]:
    """Insère ou remplace des decks dans deck_cards.

    Retourne (created, updated).
    `first_scraped_at` est préservé pour les decks déjà connus.
    """
    batch = list(batch)
    if not batch:
        return 0, 0

    deck_ids = [d.deck_id for d in batch]
    now = datetime.now(UTC)

    with engine.begin() as conn:
        # Récupérer first_scraped_at et présence pour les decks déjà en base
        rows = conn.execute(
            text("""
                SELECT deck_id, MIN(first_scraped_at) AS fsa
                FROM deck_cards
                WHERE deck_id = ANY(:ids)
                GROUP BY deck_id
            """),
            {"ids": deck_ids},
        ).fetchall()
        existing = {r.deck_id: r.fsa for r in rows}

        created = sum(1 for d in batch if d.deck_id not in existing)
        updated = len(batch) - created

        # DELETE + INSERT pour chaque deck
        conn.execute(
            text("DELETE FROM deck_cards WHERE deck_id = ANY(:ids)"),
            {"ids": deck_ids},
        )

        card_rows: list[tuple] = []
        for d in batch:
            first_scraped_at = existing.get(d.deck_id) or now
            for c in d.cards:
                card_rows.append((
                    d.deck_id,
                    d.commander,
                    c.name,
                    c.is_commander,
                    c.quantity,
                    d.bracket,
                    float(d.price) if d.price is not None else None,
                    d.currency,
                    d.deck_type,
                    d.date_created,
                    d.date_modified,
                    first_scraped_at,
                    now,
                ))

        if card_rows:
            _bulk_insert(conn, card_rows)

    return created, updated


# ── État du scraping ──────────────────────────────────────────────────────────

def known_deck_ids(engine: Engine, candidates: Iterable[str]) -> set[str]:
    """Parmi `candidates`, ceux déjà présents dans deck_cards."""
    ids = list(candidates)
    if not ids:
        return set()

    found: set[str] = set()
    for i in range(0, len(ids), 1000):
        chunk = ids[i : i + 1000]
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT deck_id FROM deck_cards WHERE deck_id = ANY(:ids)"),
                {"ids": chunk},
            )
            found.update(r[0] for r in rows)
    return found


# ── Commandants ───────────────────────────────────────────────────────────────

def upsert_commanders(engine: Engine, rows: list[dict]) -> int:
    """Insère ou met à jour des commandants (rank, color_identity).

    N'écrase pas decks_extracted, first_extracted_at, last_scraped_at.
    rows : [{"name": str, "rank": int, "color_identity": str}]
    """
    if not rows:
        return 0
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO commanders (name, rank, color_identity)
                VALUES (:name, :rank, :color_identity)
                ON CONFLICT (name) DO UPDATE
                    SET rank           = EXCLUDED.rank,
                        color_identity = EXCLUDED.color_identity
            """),
            rows,
        )
    return len(rows)


def pending_commanders(engine: Engine, *, start_rank: int = 1, refresh: bool = False) -> list[str]:
    """Commandants à scraper, ordonnés par rang."""
    where = "rank >= :r"
    if not refresh:
        where += " AND last_scraped_at IS NULL"
    with engine.connect() as conn:
        return [
            r[0] for r in conn.execute(
                text(f"SELECT name FROM commanders WHERE {where} ORDER BY rank"),
                {"r": start_rank},
            )
        ]


def mark_commander_scraped(engine: Engine, name: str, deck_count: int) -> None:
    """Met à jour le compteur et la date de dernier scrape d'un commandant."""
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO commanders (name, decks_extracted, first_extracted_at, last_scraped_at)
                VALUES (:name, :n, :now, :now)
                ON CONFLICT (name) DO UPDATE
                    SET decks_extracted    = EXCLUDED.decks_extracted,
                        first_extracted_at = COALESCE(commanders.first_extracted_at, EXCLUDED.first_extracted_at),
                        last_scraped_at    = EXCLUDED.last_scraped_at
            """),
            {"name": name, "n": deck_count, "now": now},
        )
