"""Index de classement des cartes par commandant

Revision ID: 20260903_cmd_rank_idx
Revises: 20260903_coll_card_id
Create Date: 2026-09-03

« Decks à construire » classe les cartes de chaque commandant par taux
d'inclusion sur les 3,5 M lignes de deck_stat_commander. Sans index aligné sur
cet ordre, PostgreSQL trie la table entière à chaque appel. La table appartient
au rôle applicatif, l'index est donc créable (contrairement aux tables
scryfall_* et cardmarket_*).
"""

from __future__ import annotations

from alembic import op

revision = "20260903_cmd_rank_idx"
down_revision = "20260903_coll_card_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # L'ordre des colonnes reproduit exactement celui du ROW_NUMBER() de
    # api_collection_commanders : card_name y départage les ex aequo.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_deck_stat_commander_rank
        ON deck_stat_commander (commander, inclusion_rate DESC, card_name)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_deck_stat_commander_rank")
