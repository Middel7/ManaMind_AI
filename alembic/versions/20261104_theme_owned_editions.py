"""Collections a theme : les editions declarees possedees une a une

Revision ID: 20261104_theme_owned_editions
Revises: 20261103_theme_collections
Create Date: 2026-11-04

Une edition se designe par son extension et son numero, comme dans la
collection a theme elle-meme. La declaration ne vaut que pour la collection
ou elle est faite, et ne touche pas a la vraie collection de l'utilisateur.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261104_theme_owned_editions"
down_revision = "20261103_theme_collections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "theme_collection_owned",
        sa.Column("collection_id", sa.Integer(),
                  sa.ForeignKey("theme_collections.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("set_code", sa.Text(), primary_key=True),
        sa.Column("collector_number", sa.Text(), primary_key=True),
        sa.Column("marked_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("theme_collection_owned")
