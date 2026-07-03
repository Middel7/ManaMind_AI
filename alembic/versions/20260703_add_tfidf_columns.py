"""add idf, tfidf, tfidf_norm columns to deck_stat_commander

Revision ID: 20260703_add_tfidf_columns
Revises: 20260620_add_deck_stats_tables
Create Date: 2026-07-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260703_add_tfidf_columns"
down_revision: Union[str, None] = "20260620_add_deck_stats_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("deck_stat_commander", sa.Column("idf", sa.Float(), nullable=True))
    op.add_column("deck_stat_commander", sa.Column("tfidf", sa.Float(), nullable=True))
    op.add_column("deck_stat_commander", sa.Column("tfidf_norm", sa.Float(), nullable=True))
    op.create_index("ix_deck_stat_commander_tfidf", "deck_stat_commander", ["tfidf"])


def downgrade() -> None:
    op.drop_index("ix_deck_stat_commander_tfidf", table_name="deck_stat_commander")
    op.drop_column("deck_stat_commander", "tfidf_norm")
    op.drop_column("deck_stat_commander", "tfidf")
    op.drop_column("deck_stat_commander", "idf")
