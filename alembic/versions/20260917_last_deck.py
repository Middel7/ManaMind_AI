"""Dernier deck travaille, retenu par utilisateur

Revision ID: 20260917_last_deck
Revises: 20260915_pref_printing
Create Date: 2026-09-17

Les ecrans qui demandent de choisir un deck — analyse, amelioration,
allegement, repilotage — repartaient du deck modifie le plus recemment, un
ordre qui change des qu'on touche a une liste. Le deck sur lequel on
travaille se retrouvait ainsi a etre resaisi d'un ecran a l'autre.

La colonne retient le dernier deck choisi. Elle vit dans user_profiles, avec
les autres preferences de compte : le choix suit son auteur d'un appareil a
l'autre. Aucune cle etrangere vers user_moxfield_decks — un deck supprime ne
doit pas empecher l'ecriture, et le sélecteur ignore de toute facon un
identifiant qui ne figure plus dans la liste.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260917_last_deck"
down_revision = "20260915_pref_printing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("last_deck_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "last_deck_id")
