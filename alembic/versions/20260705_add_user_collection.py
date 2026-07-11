"""add user_collection table

Revision ID: 20260705_add_user_collection
Revises: 20260703_add_commander_clusters
Create Date: 2026-07-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260705_add_user_collection"
down_revision: Union[str, None] = "20260703_add_commander_clusters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_collection",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("card_name", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("raw_line", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_user_collection_card_name", "user_collection", ["card_name"])


def downgrade() -> None:
    op.drop_index("ix_user_collection_card_name", "user_collection")
    op.drop_table("user_collection")
