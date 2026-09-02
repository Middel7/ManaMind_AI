"""Index fonctionnels pour la recherche de commandants alternatifs

Revision ID: 20260901_cmd_swap_idx
Revises: 20260714_add_quantity
Create Date: 2026-09-01

L'API /api/commander-swap joint les cartes d'une decklist sur
deck_stat_commander (3,5 M lignes) et deck_stat_global via
LOWER(TRIM(card_name)). Sans index fonctionnel, PostgreSQL fait un
seq scan complet a chaque appel.
"""

from __future__ import annotations

from alembic import op

revision = "20260901_cmd_swap_idx"
down_revision = "20260714_add_quantity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_deck_stat_commander_card_name_lower "
        "ON deck_stat_commander (LOWER(BTRIM(card_name)))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_deck_stat_global_card_name_lower "
        "ON deck_stat_global (LOWER(BTRIM(card_name)))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_deck_stat_global_card_name_lower")
    op.execute("DROP INDEX IF EXISTS ix_deck_stat_commander_card_name_lower")
