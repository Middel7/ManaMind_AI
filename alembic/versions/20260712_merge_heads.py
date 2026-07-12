"""merge clustering and card_neighbors heads

Revision ID: 20260712_merge_heads
Revises: 20260712_add_clustering_tables, 20260712_add_card_neighbors
Create Date: 2026-07-12
"""
from typing import Sequence, Union

revision: str = "20260712_merge_heads"
down_revision: Union[str, Sequence[str], None] = (
    "20260712_add_clustering_tables",
    "20260712_add_card_neighbors",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
