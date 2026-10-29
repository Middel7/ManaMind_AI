"""Decks preconstruits officiels : la liste et leurs cartes

Revision ID: 20261029_precon_decks
Revises: 20261015_password_resets
Create Date: 2026-10-29

Les decks Commander vendus tout faits par Wizards, tels que MTGJSON les
publie. Un joueur en ajoute un a ses decks en un clic, au lieu de saisir cent
cartes a la main.

precon_decks est identifie par le nom de fichier MTGJSON (« DeathToll_DSC »),
stable d'une publication a l'autre : c'est ce qui permet a une mise a jour de
ne telecharger que les decks nouveaux.

precon_deck_cards garde l'impression exacte de chaque carte (identifiant
Scryfall, edition, numero, foil) : l'ajout a la collection reprend ainsi les
cartes reellement contenues dans la boite. `zone` distingue le ou les
commandants, le reste du deck et les jetons fournis.

Deux tables propres a ManaMind : aucune ne touche au catalogue partage.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261029_precon_decks"
down_revision = "20261015_password_resets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "precon_decks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("set_code", sa.Text(), nullable=False),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("commander", sa.Text(), nullable=False),
        sa.Column("lead_scryfall_id", sa.Text(), nullable=True),
        sa.Column("card_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "precon_deck_cards",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("precon_id", sa.Text(),
                  sa.ForeignKey("precon_decks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("zone", sa.Text(), nullable=False),
        sa.Column("card_name", sa.Text(), nullable=False),
        sa.Column("scryfall_id", sa.Text(), nullable=True),
        sa.Column("set_code", sa.Text(), nullable=True),
        sa.Column("collector_number", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_foil", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("zone IN ('commander', 'main', 'token')",
                           name="ck_precon_deck_cards_zone"),
    )
    op.create_index("ix_precon_deck_cards_precon", "precon_deck_cards", ["precon_id"])


def downgrade() -> None:
    op.drop_index("ix_precon_deck_cards_precon", table_name="precon_deck_cards")
    op.drop_table("precon_deck_cards")
    op.drop_table("precon_decks")
