"""add card_neighbors table

Revision ID: 20260712_add_card_neighbors
Revises: None (branche indépendante — évite le conflit avec 20260712_add_user_opened_sets
         qui occupe déjà down_revision = "20260711_add_auth_tables")
Create Date: 2026-07-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260712_add_card_neighbors"
down_revision: Union[str, None] = None  # branche indépendante
branch_labels: Union[Sequence[str], None] = ("card_neighbors",)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "card_neighbors",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("card_name", sa.Text(), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("neighbor", sa.Text(), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.create_index("ix_card_neighbors_card_name", "card_neighbors", ["card_name"])


def downgrade() -> None:
    op.drop_index("ix_card_neighbors_card_name", table_name="card_neighbors")
    op.drop_table("card_neighbors")
