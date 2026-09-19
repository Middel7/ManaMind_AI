"""Edition preferee d'une carte, independante de la possession

Revision ID: 20260915_pref_printing
Revises: 20260904_deck_cards_id
Create Date: 2026-09-15

L'edition d'une carte n'existait que comme propriete d'une ligne de
collection : une carte que l'on ne possede pas encore n'avait aucune edition
a soi, et les ecrans lui choisissaient une illustration par heuristique — la
plus recente, hors Secret Lair et promotions.

Cette table retient l'edition qu'un utilisateur veut voir pour une carte,
qu'il la possede ou non. Les cartes sont reliees par leur nom normalise,
comme partout ailleurs dans le projet : les tables scryfall_* appartiennent
a un autre role et ne peuvent pas porter de cle etrangere vers elles.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260915_pref_printing"
down_revision = "20260904_deck_cards_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preferred_printings",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        # mm_normalize_name(nom de la carte) : la meme cle que les jointures
        # de noms du projet.
        sa.Column("card_key", sa.Text(), primary_key=True),
        sa.Column("scryfall_id", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_preferred_printings")
