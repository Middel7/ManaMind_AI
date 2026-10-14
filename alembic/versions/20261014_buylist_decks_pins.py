"""Buylist : decks ecartes de l'analyse, cartes ajoutees a la main

Revision ID: 20261014_buylist_decks_pins
Revises: 20261010_deck_bracket
Create Date: 2026-10-14

Deux reglages de la buylist, propres a chaque utilisateur.

user_buylist_excluded_decks retient les decks ecartes, et non les inclus : un
deck cree ou importe ensuite entre dans l'analyse sans rien avoir a cocher.

user_buylist_pinned retient les cartes ajoutees a la main, par cle (nom
normalise, face avant) comme user_buylist_hidden. Elles restent dans la liste
jusqu'a ce que l'utilisateur les retire.

Pas de cle etrangere vers user_moxfield_decks, qui appartient a un autre role.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261014_buylist_decks_pins"
down_revision = "20261010_deck_bracket"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_buylist_excluded_decks",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("deck_id", sa.Text(), primary_key=True),
        sa.Column("excluded_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "user_buylist_pinned",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("card_key", sa.Text(), primary_key=True),
        sa.Column("card_name", sa.Text(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_buylist_pinned")
    op.drop_table("user_buylist_excluded_decks")
