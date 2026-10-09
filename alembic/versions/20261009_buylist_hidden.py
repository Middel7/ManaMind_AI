"""Recommandations de la buylist masquees par l'utilisateur

Revision ID: 20261009_buylist_hidden
Revises: 20261002_art_cards_out
Create Date: 2026-10-09

« Ma buylist » recommande, pour chaque deck, les cartes les plus jouees par
son commandant qui lui manquent. Certaines n'ont pas leur place dans le deck
tel que l'utilisateur le concoit : il les masque, et elles ne reviennent plus.

Le masquage vaut pour un couple carte / deck, et non pour la carte : la meme
carte reste recommandable pour un autre deck, y compris un deck cree plus tard.
card_key est le nom normalise, face avant seule, comme partout ailleurs ;
card_name garde le nom affiche pour la liste des cartes masquees. Pas de cle
etrangere vers user_moxfield_decks : un deck supprime ne doit pas faire
echouer l'ecriture, comme pour les jetons et le commandant principal.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261009_buylist_hidden"
down_revision = "20261002_art_cards_out"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_buylist_hidden",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("deck_id", sa.Text(), primary_key=True),
        sa.Column("card_key", sa.Text(), primary_key=True),
        sa.Column("card_name", sa.Text(), nullable=False),
        sa.Column("hidden_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_buylist_hidden")
