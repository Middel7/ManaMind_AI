"""Courbe de mana de reference, par commandant

Revision ID: 20260917_cmd_curve
Revises: 20260917_last_deck
Create Date: 2026-09-17

La courbe de mana d'un deck ne dit rien seule : un cout moyen de 2,8 est bas
chez un commandant et haut chez un autre. La comparaison utile est celle des
decks qui jouent le meme commandant.

Cette moyenne se lit dans deck_cards, mais la table pese pres de cinq cent
mille decks : la recalculer a chaque affichage couterait une seconde par page.
La table la retient, comme deck_stat_commander retient les taux d'inclusion.
Elle se remplit au fil des demandes et se reecrit apres chaque scrape.

curve porte les huit parts de la courbe (couts 0 a 7 et plus), en pourcentage
des sorts, moyennees deck par deck — et non sur le tas de cartes, pour qu'un
deck ne pese pas plus qu'un autre selon sa taille.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260917_cmd_curve"
down_revision = "20260917_last_deck"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deck_stat_commander_curve",
        sa.Column("commander", sa.Text(), primary_key=True),
        sa.Column("decks", sa.Integer(), nullable=False),
        sa.Column("avg_mana_value", sa.Numeric(6, 3), nullable=False),
        sa.Column("curve", postgresql.ARRAY(sa.Numeric(6, 3)), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("deck_stat_commander_curve")
