"""Bracket d'un deck choisi a la main

Revision ID: 20261010_deck_bracket
Revises: 20261009_buylist_hidden
Create Date: 2026-10-10

Le bracket d'un deck est calcule a la lecture — Game Changers, destruction
massive de terrains, tours supplementaires — et n'est pas stocke. Mais le
calcul ne voit ni les combos ni l'intention du joueur : celui-ci peut le
corriger, et c'est ce choix seul que la table retient.

Meme forme que user_deck_lead : user_moxfield_decks n'accepte pas de colonne
nouvelle, et pas de cle etrangere vers elle.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261010_deck_bracket"
down_revision = "20261009_buylist_hidden"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_deck_bracket",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("deck_id", sa.Text(), primary_key=True),
        sa.Column("bracket", sa.SmallInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("bracket BETWEEN 1 AND 5", name="ck_user_deck_bracket_range"),
    )


def downgrade() -> None:
    op.drop_table("user_deck_bracket")
