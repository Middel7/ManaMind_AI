"""fonction SQL de normalisation des noms de cartes

scryfall_cards.normalized_name est produit cote Python par
mtgdb.db.models.card.normalize_card_name : NFD, suppression des marques
diacritiques, strip, minuscules. Un simple LOWER(TRIM(...)) en SQL ne
reproduit pas cette regle et fait echouer la jointure pour les 132 cartes
accentuees (Lorien Revealed, Kharn the Betrayer, Mjolnir...).

Cette migration expose la meme regle en SQL, et indexe user_collection et
user_deck_cards dessus pour que les jointures restent indexees.

Revision ID: 20260903_norm_name
Revises: 20260902_collection_v2
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op

revision: str = "20260903_norm_name"
down_revision: Union[str, None] = "20260902_collection_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(r"""
        CREATE OR REPLACE FUNCTION mm_normalize_name(value text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        RETURNS NULL ON NULL INPUT
        AS $$
            SELECT lower(btrim(
                regexp_replace(normalize(value, NFD), '[̀-ͯ]', '', 'g')
            ));
        $$;
    """)

    # user_deck_cards appartient au role postgres : on ne peut pas l'indexer ici.
    # Seule user_collection nous appartient.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_user_collection_norm_name
        ON user_collection (user_id, mm_normalize_name(card_name))
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_user_collection_norm_name")
    op.execute("DROP FUNCTION IF EXISTS mm_normalize_name(text)")
