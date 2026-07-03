"""add commander_clusters table

Revision ID: 20260703_add_commander_clusters
Revises: 20260703_add_deck_cards_table
Create Date: 2026-07-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260703_add_commander_clusters"
down_revision: Union[str, None] = "20260703_add_deck_cards_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Clusters par commandant : une ligne par (commandant, cluster, carte)
    op.create_table(
        "commander_clusters",
        sa.Column("id",            sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("commander",     sa.Text(), nullable=False),
        sa.Column("cluster_id",    sa.Integer(), nullable=False),   # local au commandant, 0-based
        sa.Column("card_name",     sa.Text(), nullable=False),
        sa.Column("deck_count",    sa.Integer(), nullable=False),   # nb decks de ce commandant contenant cette carte
        sa.Column("total_decks",   sa.Integer(), nullable=False),   # nb total decks du commandant
        sa.Column("inclusion_rate",sa.Float(), nullable=False),     # deck_count / total_decks * 100
    )
    op.create_index("ix_cc_commander",          "commander_clusters", ["commander"])
    op.create_index("ix_cc_commander_cluster",  "commander_clusters", ["commander", "cluster_id"])
    op.create_index("ix_cc_card_name",          "commander_clusters", ["card_name"])

    # Métadonnées par cluster (nom, taille, nb decks où il apparaît)
    op.create_table(
        "commander_cluster_meta",
        sa.Column("id",             sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("commander",      sa.Text(), nullable=False),
        sa.Column("cluster_id",     sa.Integer(), nullable=False),
        sa.Column("cluster_label",  sa.Text(), nullable=True),      # nom lisible généré automatiquement
        sa.Column("card_count",     sa.Integer(), nullable=False),  # nb cartes dans ce cluster
        sa.Column("deck_presence",  sa.Integer(), nullable=False),  # nb decks contenant ≥1 carte du cluster
        sa.Column("total_decks",    sa.Integer(), nullable=False),
        sa.Column("presence_rate",  sa.Float(), nullable=False),    # deck_presence / total_decks * 100
        sa.Column("top_cards",      sa.Text(), nullable=True),      # JSON array des 5 cartes principales
    )
    op.create_index("ix_ccm_commander",         "commander_cluster_meta", ["commander"])
    op.create_index("ix_ccm_commander_cluster", "commander_cluster_meta", ["commander", "cluster_id"])

    # Table de progression pour reprise sur crash
    op.create_table(
        "commander_cluster_progress",
        sa.Column("commander",      sa.Text(), primary_key=True),
        sa.Column("n_clusters",     sa.Integer(), nullable=False),
        sa.Column("computed_at",    sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("commander_cluster_progress")
    op.drop_index("ix_ccm_commander_cluster", table_name="commander_cluster_meta")
    op.drop_index("ix_ccm_commander",         table_name="commander_cluster_meta")
    op.drop_table("commander_cluster_meta")
    op.drop_index("ix_cc_card_name",          table_name="commander_clusters")
    op.drop_index("ix_cc_commander_cluster",  table_name="commander_clusters")
    op.drop_index("ix_cc_commander",          table_name="commander_clusters")
    op.drop_table("commander_clusters")
