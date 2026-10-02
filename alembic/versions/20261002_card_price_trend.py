"""Prix de reference : tendance Cardmarket de l'edition la moins chere

Revision ID: 20261002_price_trend
Revises: 20261002_deck_lead
Create Date: 2026-10-02

Le prix de reference passait par le low_price, la meilleure offre du moment.
Il suit desormais la tendance (trend_price) : moins sensible a une annonce
isolee bradee, plus proche de ce qu'une carte se vend reellement. On garde
l'edition la moins chere, au sens de cette tendance.

Une impression sur quarante environ a un low_price sans tendance. Une carte
dont aucune edition n'a de tendance garde son low_price plutot que de passer
pour gratuite.

La colonne s'appelle desormais `price` : la laisser sous le nom low_price
aurait fait mentir chaque requete qui la lit.

A rafraichir apres chaque import de prix Cardmarket :

    REFRESH MATERIALIZED VIEW CONCURRENTLY card_min_price;
"""

from __future__ import annotations

from alembic import op

revision = "20261002_price_trend"
down_revision = "20261002_deck_lead"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS card_min_price")
    op.execute("""
        CREATE MATERIALIZED VIEW card_min_price AS
        SELECT p.card_id,
               COALESCE(
                   MIN(latest.trend_price) FILTER (WHERE latest.trend_price > 0),
                   MIN(latest.low_price)   FILTER (WHERE latest.low_price > 0)
               ) AS price
        FROM scryfall_card_printings p
        CROSS JOIN LATERAL (
            SELECT pge.low_price, pge.trend_price
            FROM cardmarket_price_guide_entries pge
            WHERE pge.id_product = p.cardmarket_id
            ORDER BY pge.captured_at DESC
            LIMIT 1
        ) latest
        WHERE p.cardmarket_id IS NOT NULL
          AND (latest.trend_price > 0 OR latest.low_price > 0)
        GROUP BY p.card_id
    """)
    # Index unique : indispensable pour un REFRESH CONCURRENTLY.
    op.execute("CREATE UNIQUE INDEX ix_card_min_price_card_id ON card_min_price (card_id)")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS card_min_price")
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
    op.execute("CREATE UNIQUE INDEX ix_card_min_price_card_id ON card_min_price (card_id)")
