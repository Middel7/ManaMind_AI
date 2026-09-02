"""Refactor : étend deck_cards + crée commanders + supprime tables mox_*

Revision ID: 20260713_refactor_mox
Revises: 20260712_fix_ucoll
Create Date: 2026-07-13

Changements :
- deck_cards : 8 nouvelles colonnes de métadonnées (bracket, price, currency,
  deck_type, date_created, date_modified, first_scraped_at, scraped_at)
- Nouvelle table commanders (remplace mox_commanders, gérée par Alembic)
- DROP mox_deck_cards, mox_decks, mox_commanders (données non migrées — table rase)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260713_refactor_mox"
down_revision = "20260712_fix_ucoll"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Nouvelles colonnes sur deck_cards ──────────────────────────────
    op.add_column("deck_cards", sa.Column("bracket",        sa.SmallInteger(),              nullable=True))
    op.add_column("deck_cards", sa.Column("price",          sa.Numeric(10, 2),              nullable=True))
    op.add_column("deck_cards", sa.Column("currency",       sa.String(4),                   nullable=True))
    op.add_column("deck_cards", sa.Column("deck_type",      sa.String(16),                  nullable=True))
    op.add_column("deck_cards", sa.Column("date_created",   sa.DateTime(timezone=True),     nullable=True))
    op.add_column("deck_cards", sa.Column("date_modified",  sa.DateTime(timezone=True),     nullable=True))
    op.add_column("deck_cards", sa.Column("first_scraped_at", sa.DateTime(timezone=True),   nullable=True))
    op.add_column("deck_cards", sa.Column("scraped_at",     sa.DateTime(timezone=True),     nullable=True))

    # ── 2. Nouvelle table commanders ──────────────────────────────────────
    op.create_table(
        "commanders",
        sa.Column("name",               sa.String(255), primary_key=True),
        sa.Column("rank",               sa.Integer(),   nullable=True),
        sa.Column("color_identity",     sa.String(16),  nullable=True),
        sa.Column("decks_extracted",    sa.Integer(),   nullable=True),
        sa.Column("first_extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scraped_at",    sa.DateTime(timezone=True), nullable=True),
    )

    # ── 3. Suppression des tables mox_* (FK d'abord) ─────────────────────
    # mox_deck_cards a une FK vers mox_decks → on la supprime en premier.
    # Ces tables peuvent ne pas exister si jamais l'environnement est vierge ;
    # on utilise IF EXISTS pour que la migration reste idempotente.
    op.execute("DROP TABLE IF EXISTS mox_deck_cards")
    op.execute("DROP TABLE IF EXISTS mox_decks")
    op.execute("DROP TABLE IF EXISTS mox_commanders")


def downgrade() -> None:
    # Supprime les colonnes ajoutées et la table commanders.
    # Les tables mox_* ne sont PAS recréées (données perdues — downgrade partiel).
    op.drop_table("commanders")

    for col in ("scraped_at", "first_scraped_at", "date_modified", "date_created",
                "deck_type", "currency", "price", "bracket"):
        op.drop_column("deck_cards", col)
