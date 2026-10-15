"""Mot de passe oublie : liens de reinitialisation

Revision ID: 20261015_password_resets
Revises: 20261014_buylist_decks_pins
Create Date: 2026-10-15

Un lien envoye par e-mail porte un jeton tire au hasard ; la table n'en garde
que l'empreinte SHA-256, jamais le jeton lui-meme. Une ligne sert une fois
(used_at) et expire au bout d'une heure (expires_at).

La table est creee par ManaMind et lui appartient : l'index sur user_id ne
touche a aucune table du catalogue partage.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261015_password_resets"
down_revision = "20261014_buylist_decks_pins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_resets",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_password_resets_user_id", "password_resets", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_password_resets_user_id", table_name="password_resets")
    op.drop_table("password_resets")
