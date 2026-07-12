"""add clustering tables: card_clusters_global, card_tag_clusters, tag_cluster_probabilities

Revision ID: 20260712_add_clustering_tables
Revises: 20260712_add_user_opened_sets
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_add_clustering_tables"
down_revision: Union[str, None] = "20260712_add_user_opened_sets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Table card_clusters_global ────────────────────────────────────────────
    # Cluster global par carte (source : card_cluster_full.csv, ~31 730 cartes)
    op.create_table(
        "card_clusters_global",
        sa.Column("card_name", sa.Text(), primary_key=True),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("global_frequency", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("is_noise_fallback", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_card_clusters_global_cluster_id", "card_clusters_global", ["cluster_id"])

    # ── Table card_tag_clusters ───────────────────────────────────────────────
    # Une ligne par (carte, tag) avec cluster associé (source : tag_cluster_dataset.csv, ~85 638 lignes)
    op.create_table(
        "card_tag_clusters",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("card_name", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("cluster_name", sa.Text(), nullable=True),
        sa.Column("tag", sa.Text(), nullable=False),
    )
    op.create_index("ix_card_tag_clusters_card_name", "card_tag_clusters", ["card_name"])
    op.create_index("ix_card_tag_clusters_tag", "card_tag_clusters", ["tag"])
    op.create_index("ix_card_tag_clusters_cluster_id", "card_tag_clusters", ["cluster_id"])

    # ── Table tag_cluster_probabilities ───────────────────────────────────────
    # Probabilité P(cluster | tag) par tag (source : tag_to_cluster.csv, ~31 298 lignes)
    op.create_table(
        "tag_cluster_probabilities",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("cluster_name", sa.Text(), nullable=True),
        sa.Column("count_cards", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("probability", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.create_index("ix_tag_cluster_probabilities_tag", "tag_cluster_probabilities", ["tag"])
    op.create_index(
        "uq_tag_cluster_probabilities",
        "tag_cluster_probabilities",
        ["tag", "cluster_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_tag_cluster_probabilities", table_name="tag_cluster_probabilities")
    op.drop_index("ix_tag_cluster_probabilities_tag", table_name="tag_cluster_probabilities")
    op.drop_table("tag_cluster_probabilities")

    op.drop_index("ix_card_tag_clusters_cluster_id", table_name="card_tag_clusters")
    op.drop_index("ix_card_tag_clusters_tag", table_name="card_tag_clusters")
    op.drop_index("ix_card_tag_clusters_card_name", table_name="card_tag_clusters")
    op.drop_table("card_tag_clusters")

    op.drop_index("ix_card_clusters_global_cluster_id", table_name="card_clusters_global")
    op.drop_table("card_clusters_global")
