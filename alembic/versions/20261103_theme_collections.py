"""Collections a theme : leurs criteres, et les cartes declarees completes

Revision ID: 20261103_theme_collections
Revises: 20261029_precon_decks
Create Date: 2026-11-03

theme_collections enregistre les criteres d'une collection, pas sa liste :
elle est reevaluee a chaque ouverture, et les cartes d'une extension nouvelle
y entrent d'elles-memes.

theme_collection_complete retient les cartes dont l'utilisateur declare
posseder toutes les editions, pour cette collection seulement. Pas de cle
etrangere vers scryfall_cards, qui appartient a un autre role.

Deux tables propres a ManaMind : aucune ne touche au catalogue partage.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261103_theme_collections"
down_revision = "20261029_precon_decks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "theme_collections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False, server_default="en"),
        sa.Column("criteria", postgresql.JSONB(), nullable=False),
        sa.Column("include_alt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_theme_collections_user", "theme_collections", ["user_id"])
    op.create_table(
        "theme_collection_complete",
        sa.Column("collection_id", sa.Integer(),
                  sa.ForeignKey("theme_collections.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("card_id", sa.Integer(), primary_key=True),
        sa.Column("marked_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("theme_collection_complete")
    op.drop_index("ix_theme_collections_user", table_name="theme_collections")
    op.drop_table("theme_collections")
