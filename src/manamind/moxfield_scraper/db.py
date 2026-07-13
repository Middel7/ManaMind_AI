"""Persistance SQL — ne connaît rien de Moxfield, juste des Deck.

Le schéma est déclaré en SQLAlchemy Core (pas d'ORM) : l'upsert s'adapte au
dialecte, donc le même code tourne sur Postgres et sur SQLite (tests).
La base porte l'état du scraping : c'est elle qui dit ce qui est déjà connu,
ce qui remplace les "le fichier existe-t-il ?" de l'ancien pipeline.

Les tables sont préfixées `mox_` et vivent dans un MetaData qui leur est propre,
séparé de `mtgdb.db.base.Base`. C'est délibéré : le scraper reste une zone
autonome qui ne peut pas écraser `deck_cards` (le schéma ManaMind existant, de
forme différente). L'intégration aux tables ManaMind — si elle est souhaitée —
se fait en réécrivant `upsert_decks()`, sans toucher au reste du package.
"""

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Table,
    create_engine,
    delete,
    select,
)
from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from .models import Deck

metadata = MetaData()

decks = Table(
    "mox_decks",
    metadata,
    Column("deck_id", String(32), primary_key=True),
    Column("commander", String(255), index=True),
    Column("deck_type", String(16), nullable=False, default=""),
    Column("bracket", SmallInteger),
    Column("price", Numeric(10, 2)),
    Column("currency", String(4), nullable=False, default=""),
    Column("date_created", DateTime(timezone=True)),
    Column("date_modified", DateTime(timezone=True), index=True),
    Column("first_scraped_at", DateTime(timezone=True), nullable=False),  # immuable
    Column("scraped_at", DateTime(timezone=True), nullable=False),         # mis à jour à chaque scrape
)

deck_cards = Table(
    "mox_deck_cards",
    metadata,
    Column(
        "deck_id",
        String(32),
        ForeignKey("mox_decks.deck_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("card_name", String(255), primary_key=True),
    Column("quantity", Integer, nullable=False),
    Column("is_commander", SmallInteger, nullable=False, default=0),
    Index("ix_mox_deck_cards_card_name", "card_name"),
)

# Remplace TOPCOMMANDER.csv : même rôle (liste ordonnée + état de reprise),
# mais transactionnel et interrogeable.
commanders = Table(
    "mox_commanders",
    metadata,
    Column("name", String(255), primary_key=True),
    Column("rank", Integer, index=True),
    Column("color_identity", String(16)),
    Column("decks_extracted", Integer),
    Column("last_scraped_at", DateTime(timezone=True)),
)


def make_engine(url: str, **kwargs: object) -> Engine:
    """url : postgresql://user:pwd@host/db (le DATABASE_URL du .env) ou sqlite:///mox.db"""
    return create_engine(url, future=True, **kwargs)


def init_schema(engine: Engine) -> None:
    metadata.create_all(engine)
    _backfill_dates(engine)


def _backfill_dates(engine: Engine) -> None:
    """Ajoute first_scraped_at si absent (upgrade depuis ancien schéma) et
    remplace les NULLs sur les deux colonnes de dates par NOW()."""
    with engine.begin() as conn:
        dialect = conn.dialect.name

        # PostgreSQL et SQLite supportent tous les deux ADD COLUMN
        # (SQLite depuis 3.37, PostgreSQL depuis toujours).
        if dialect == "postgresql":
            conn.execute(text(
                "ALTER TABLE mox_decks "
                "ADD COLUMN IF NOT EXISTS first_scraped_at TIMESTAMPTZ"
            ))
        elif dialect == "sqlite":
            # SQLite : ADD COLUMN échoue si la colonne existe déjà
            try:
                conn.execute(text(
                    "ALTER TABLE mox_decks ADD COLUMN first_scraped_at DATETIME"
                ))
            except Exception:
                pass  # colonne déjà présente

        # Backfill NULLs avec la date du jour
        now_expr = "NOW()" if dialect == "postgresql" else "datetime('now')"
        conn.execute(text(
            f"UPDATE mox_decks SET first_scraped_at = {now_expr} "
            f"WHERE first_scraped_at IS NULL"
        ))
        conn.execute(text(
            f"UPDATE mox_decks SET scraped_at = {now_expr} "
            f"WHERE scraped_at IS NULL"
        ))


# ─── Upsert ─────────────────────────────────────────────────────────────────

def _upsert(conn: Connection, table: Table, rows: list[dict], update_cols: list[str]) -> None:
    """INSERT ... ON CONFLICT DO UPDATE, dans la syntaxe du dialecte courant."""
    if not rows:
        return

    dialect = conn.dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(table).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[c.name for c in table.primary_key],
            set_={c: stmt.excluded[c] for c in update_cols},
        )
    elif dialect in ("mysql", "mariadb"):
        stmt = mysql_insert(table).values(rows)
        stmt = stmt.on_duplicate_key_update(
            **{c: stmt.inserted[c] for c in update_cols}
        )
    elif dialect == "sqlite":
        stmt = sqlite_insert(table).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[c.name for c in table.primary_key],
            set_={c: stmt.excluded[c] for c in update_cols},
        )
    else:
        raise NotImplementedError(f"Dialecte non supporté pour l'upsert : {dialect}")

    conn.execute(stmt)


def upsert_decks(engine: Engine, batch: Iterable[Deck]) -> tuple[int, int]:
    """Insère ou met à jour des decks avec leurs cartes, en une transaction.

    Les cartes sont remplacées (delete + insert) et non fusionnées : un deck
    modifié sur Moxfield doit refléter sa nouvelle liste, pas l'union des deux.

    Retourne (created, updated) : nombre de decks créés et mis à jour.
    """
    batch = list(batch)
    if not batch:
        return 0, 0

    deck_ids = [d.deck_id for d in batch]

    # Identifier les decks déjà présents avant l'upsert
    with engine.connect() as conn:
        rows = conn.execute(select(decks.c.deck_id).where(decks.c.deck_id.in_(deck_ids)))
        existing_ids = {r[0] for r in rows}

    updated = len(existing_ids)
    created = len(batch) - updated

    now = datetime.now(UTC)
    deck_rows = [
        {
            "deck_id": d.deck_id,
            "commander": d.commander,
            "deck_type": d.deck_type,
            "bracket": d.bracket,
            "price": d.price,
            "currency": d.currency,
            "date_created": d.date_created,
            "date_modified": d.date_modified,
            "first_scraped_at": now,
            "scraped_at": now,
        }
        for d in batch
    ]
    card_rows = [
        {
            "deck_id": d.deck_id,
            "card_name": c.name,
            "quantity": c.quantity,
            "is_commander": int(c.is_commander),
        }
        for d in batch
        for c in d.cards
    ]

    with engine.begin() as conn:
        _upsert(
            conn,
            decks,
            deck_rows,
            # first_scraped_at absent de la liste → jamais écrasé sur UPDATE
            ["commander", "deck_type", "bracket", "price", "currency",
             "date_created", "date_modified", "scraped_at"],
        )
        conn.execute(delete(deck_cards).where(deck_cards.c.deck_id.in_(deck_ids)))
        if card_rows:
            conn.execute(deck_cards.insert(), card_rows)

    return created, updated


# ─── État du scraping ───────────────────────────────────────────────────────

def known_deck_ids(engine: Engine, candidates: Iterable[str]) -> set[str]:
    """Parmi `candidates`, ceux déjà en base. Sert à ne télécharger que le neuf."""
    ids = list(candidates)
    if not ids:
        return set()

    found: set[str] = set()
    # Découpé : certains drivers plafonnent le nombre de paramètres liés.
    for i in range(0, len(ids), 1000):
        chunk = ids[i : i + 1000]
        with engine.connect() as conn:
            rows = conn.execute(select(decks.c.deck_id).where(decks.c.deck_id.in_(chunk)))
            found.update(r[0] for r in rows)
    return found


def upsert_commanders(engine: Engine, rows: list[dict]) -> int:
    """rows : [{"name":..., "rank":..., "color_identity":...}].

    N'écrase pas l'état de scraping (decks_extracted, last_scraped_at).
    """
    if not rows:
        return 0
    with engine.begin() as conn:
        _upsert(conn, commanders, rows, ["rank", "color_identity"])
    return len(rows)


def pending_commanders(engine: Engine, *, start_rank: int = 1, refresh: bool = False) -> list[str]:
    """Commandants à traiter, par rang croissant. Ceux déjà scrapés sont exclus
    sauf si `refresh` (re-scan complet pour récupérer les nouveaux decks)."""
    query = select(commanders.c.name).where(commanders.c.rank >= start_rank)
    if not refresh:
        query = query.where(commanders.c.last_scraped_at.is_(None))
    query = query.order_by(commanders.c.rank)

    with engine.connect() as conn:
        return [r[0] for r in conn.execute(query)]


def mark_commander_scraped(engine: Engine, name: str, deck_count: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            commanders.update()
            .where(commanders.c.name == name)
            .values(decks_extracted=deck_count, last_scraped_at=datetime.now(UTC))
        )
