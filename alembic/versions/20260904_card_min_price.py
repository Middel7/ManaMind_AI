"""Prix de reference par carte : low_price de l'edition la moins chere

Revision ID: 20260904_card_min_price
Revises: 20260904_hidden_moves
Create Date: 2026-09-04

Le prix affiche partout dans l'application est le low_price Cardmarket de
l'edition la moins chere. Le calculer a la volee demandait, pour chaque carte,
de parcourir toutes ses impressions puis la derniere entree de prix de chacune
— 526 ms pour une page de collection, une seconde pour l'accueil.

La vue le precalcule en 2,6 s pour les 33 887 cartes cotees. A rafraichir
apres chaque import de prix Cardmarket :

    REFRESH MATERIALIZED VIEW CONCURRENTLY card_min_price;
"""

from __future__ import annotations

from alembic import op

revision = "20260904_card_min_price"
down_revision = "20260904_hidden_moves"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE MATERIALIZED VIEW card_min_price AS
        SELECT p.card_id,
               MIN(latest.low_price) AS low_price
        FROM scryfall_card_printings p
        CROSS JOIN LATERAL (
            SELECT pge.low_price
            FROM cardmarket_price_guide_entries pge
            WHERE pge.id_product = p.cardmarket_id
            ORDER BY pge.captured_at DESC
            LIMIT 1
        ) latest
        WHERE p.cardmarket_id IS NOT NULL
          AND latest.low_price > 0
        GROUP BY p.card_id
    """)
    # Index unique : indispensable pour un REFRESH CONCURRENTLY, qui evite de
    # bloquer les lectures pendant le rafraichissement.
    op.execute("CREATE UNIQUE INDEX ix_card_min_price_card_id ON card_min_price (card_id)")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS card_min_price")
