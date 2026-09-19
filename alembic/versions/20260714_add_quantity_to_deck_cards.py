"""deck_cards : ajout colonne quantity

Revision ID: 20260714_add_quantity
Revises: 20260713_refactor_mox
Create Date: 2026-07-14
"""

from __future__ import annotations
import sqlalchemy as sa
from alembic import op

revision = "20260714_add_quantity"
down_revision = "20260713_refactor_mox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deck_cards",
        sa.Column("quantity", sa.SmallInteger(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("deck_cards", "quantity")
