"""Commandant principal d'un deck a deux commandants

Revision ID: 20261002_deck_lead
Revises: 20260921_feedback
Create Date: 2026-10-02

Une paire de commandants s'ecrit par ordre alphabetique (« Acolyte of
Bahamut & Shadowheart, Dark Justiciar ») : c'est sous cette forme que les
statistiques publiques l'indexent, et elle ne peut pas changer. Mais un joueur
pense son deck autour de l'un des deux — la creature, pas son Background —, et
c'est elle qui doit en porter l'illustration et ouvrir la liste.

La table retient ce choix a part. user_moxfield_decks appartient a un autre
role et n'accepte pas de colonne nouvelle. Pas de cle etrangere vers elle : un
deck supprime ne doit pas faire echouer l'ecriture, comme pour les jetons. Un
nom qui n'est plus commandant du deck est simplement ignore a la lecture.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261002_deck_lead"
down_revision = "20260921_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_deck_lead",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("deck_id", sa.Text(), primary_key=True),
        sa.Column("card_name", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_deck_lead")
