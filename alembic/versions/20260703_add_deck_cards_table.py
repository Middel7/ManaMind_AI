"""add deck_cards table for individual decklists

Revision ID: 20260703_add_deck_cards_table
Revises: 20260703_add_tfidf_columns
Create Date: 2026-07-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260703_add_deck_cards_table"
down_revision: Union[str, None] = "20260703_add_tfidf_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deck_cards",
        sa.Column("id",           sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("deck_id",      sa.Text(), nullable=False),
        sa.Column("commander",    sa.Text(), nullable=False),
        sa.Column("card_name",    sa.Text(), nullable=False),
        sa.Column("is_commander", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_deck_cards_commander", "deck_cards", ["commander"])
    op.create_index("ix_deck_cards_card_name", "deck_cards", ["card_name"])
    op.create_index("ix_deck_cards_deck_id",   "deck_cards", ["deck_id"])

    # Table de progression pour reprise sur crash
    op.create_table(
        "deck_cards_import_progress",
        sa.Column("commander", sa.Text(), primary_key=True),
        sa.Column("decks_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_index("ix_deck_cards_deck_id",   table_name="deck_cards")
    op.drop_index("ix_deck_cards_card_name", table_name="deck_cards")
    op.drop_index("ix_deck_cards_commander", table_name="deck_cards")
    op.drop_table("deck_cards")
    op.drop_table("deck_cards_import_progress")
