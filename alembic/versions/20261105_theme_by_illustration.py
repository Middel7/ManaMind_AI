"""Collections a theme : compter les illustrations plutot que les editions

Revision ID: 20261105_theme_by_illustration
Revises: 20261104_theme_owned_editions
Create Date: 2026-11-05

Avec by_illustration, les editions d'une carte qui reprennent la meme image
ne comptent qu'une fois : en posseder une suffit. Le defaut garde le
decompte par edition des collections existantes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261105_theme_by_illustration"
down_revision = "20261104_theme_owned_editions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("theme_collections",
                  sa.Column("by_illustration", sa.Boolean(), nullable=False,
                            server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("theme_collections", "by_illustration")
