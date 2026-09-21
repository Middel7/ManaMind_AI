"""Retours des utilisateurs sur l'application

Revision ID: 20260921_feedback
Revises: 20260920_deck_tokens
Create Date: 2026-09-21

Rien ne permettait a un utilisateur de dire ce qui manque ou ce qui cloche. Le
bouton « Help me improve » ouvre une fenetre de saisie ; ce qui s'y ecrit
atterrit ici, et se lit dans l'ecran d'administration.

Le message est conserve tel quel, sans traitement : c'est du texte libre, rendu
dans l'interface avec echappement. `handled_at` distingue ce qui a ete traite
de ce qui vient d'arriver — une date plutot qu'un booleen, parce qu'elle dit
aussi quand.

La cle etrangere vers users porte ON DELETE CASCADE : un compte supprime
emporte ses retours. Le pseudo n'est pas recopie ici, il se lit par jointure —
ainsi un changement de pseudo se refleteRA sur les anciens messages.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260921_feedback"
down_revision = "20260920_deck_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
    )
    # L'ecran d'administration lit toujours dans le meme ordre : les non traites
    # d'abord, les plus recents en tete.
    op.create_index(
        "ix_user_feedback_tri", "user_feedback",
        [sa.text("handled_at NULLS FIRST"), sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("user_feedback")
