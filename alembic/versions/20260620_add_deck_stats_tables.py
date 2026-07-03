"""add deck_stat_commander and deck_stat_global tables

Revision ID: 20260620_add_deck_stats_tables
Revises: a1b2c3d4e5f6
Create Date: 2026-06-20 00:00:00.000000

Ces tables ont été créées directement en base via mtgdb.
Ce fichier de migration les documente pour que l'historique Alembic soit cohérent.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260620_add_deck_stats_tables"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deck_stat_commander",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("commander", sa.String(length=255), nullable=False),
        sa.Column("card_name", sa.Text(), nullable=False),
        sa.Column("decks_with_card", sa.Integer(), nullable=False),
        sa.Column("total_decks", sa.Integer(), nullable=False),
        sa.Column("inclusion_rate", sa.Float(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("commander", "card_name", name="uq_deck_stat_commander_card"),
    )
    op.create_index("ix_deck_stat_commander_commander", "deck_stat_commander", ["commander"])
    op.create_index("ix_deck_stat_commander_card_name", "deck_stat_commander", ["card_name"])

    op.create_table(
        "deck_stat_global",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("card_name", sa.Text(), nullable=False),
        sa.Column("decks_count", sa.BigInteger(), nullable=False),
        sa.Column("total_decks", sa.BigInteger(), nullable=False),
        sa.Column("global_frequency", sa.Float(), nullable=False),
        sa.Column("commanders_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idf", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("card_name", name="uq_deck_stat_global_card_name"),
    )
    op.create_index("ix_deck_stat_global_card_name", "deck_stat_global", ["card_name"])


def downgrade() -> None:
    op.drop_index("ix_deck_stat_global_card_name", table_name="deck_stat_global")
    op.drop_table("deck_stat_global")
    op.drop_index("ix_deck_stat_commander_card_name", table_name="deck_stat_commander")
    op.drop_index("ix_deck_stat_commander_commander", table_name="deck_stat_commander")
    op.drop_table("deck_stat_commander")
