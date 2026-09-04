"""Suggestions de deplacement masquees par l'utilisateur

Revision ID: 20260904_hidden_moves
Revises: 20260903_cmd_rank_idx
Create Date: 2026-09-04

« Cartes a changer de deck » propose des deplacements que l'on ne veut pas
toujours appliquer. Les ecarter une fois doit suffire : la table retient les
suggestions refusees pour qu'elles ne reviennent pas a chaque analyse.

Une suggestion est identifiee par le trio carte / deck d'origine / deck
d'accueil : la meme carte peut valoir un deplacement ailleurs.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260904_hidden_moves"
down_revision = "20260903_cmd_rank_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_hidden_moves",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("card_name", sa.Text(), nullable=False),
        sa.Column("from_commander", sa.Text(), nullable=False),
        sa.Column("to_commander", sa.Text(), nullable=False),
        sa.Column("hidden_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "card_name", "from_commander", "to_commander",
                            name="uq_user_hidden_move"),
    )
    op.create_index("ix_user_hidden_moves_user_id", "user_hidden_moves", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_hidden_moves_user_id", table_name="user_hidden_moves")
    op.drop_table("user_hidden_moves")
