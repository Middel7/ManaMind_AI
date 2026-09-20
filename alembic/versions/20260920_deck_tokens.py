"""Jetons d'un deck, suivis a part des cartes

Revision ID: 20260920_deck_tokens
Revises: 20260917_cmd_curve
Create Date: 2026-09-20

Un deck Commander joue cent cartes, mais met parfois en jeu une vingtaine de
jetons qui ne figurent nulle part dans la liste. Les oublier ne se voit qu'a
table, quand personne n'a de Tresor sous la main.

Les jetons ne vont pas dans user_deck_cards : cette table appartient a un
autre role, ses lignes alimentent le compte de cartes, la courbe de mana et la
valeur du deck — un jeton y ferait passer le deck a 101 cartes. Elle vit donc
ici, a cote, et n'entre dans aucun de ces calculs.

token_key est l'oracle_id du jeton quand il a pu etre resolu dans le
catalogue, et son nom normalise sinon : un jeton reimprime dans quinze
editions ne doit compter qu'une fois, et un jeton dont l'impression citee
manque du catalogue doit rester ajoutable.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_deck_tokens"
down_revision = "20260917_cmd_curve"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_deck_tokens",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        # Pas de cle etrangere vers user_moxfield_decks : un deck supprime ne
        # doit pas faire echouer l'ecriture, et la lecture filtre deja sur le
        # deck demande. C'est le choix deja fait pour user_deck_cards.
        sa.Column("deck_id", sa.Text(), primary_key=True),
        sa.Column("token_key", sa.Text(), primary_key=True),
        # Nom et type recopies : ils permettent d'afficher la ligne sans
        # rejoindre le catalogue, y compris pour un jeton qui en a disparu.
        sa.Column("token_name", sa.Text(), nullable=False),
        sa.Column("token_type_line", sa.Text(), nullable=True),
        sa.Column("quantity", sa.SmallInteger(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_deck_tokens")
