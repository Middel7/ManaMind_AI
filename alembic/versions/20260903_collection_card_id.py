"""lien direct entre un exemplaire et la carte Scryfall

Chaque lecture de la collection resolvait le nom de carte a la volee (LATERAL
sur normalized_name pour chaque ligne), ce qui coutait plus d'une seconde sur
2 000 exemplaires. On materialise la resolution : card_id pointe vers
scryfall_cards, printing_id vers l'impression exacte quand elle est connue.

Revision ID: 20260903_coll_card_id
Revises: 20260903_norm_name
Create Date: 2026-09-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_coll_card_id"
down_revision: Union[str, None] = "20260903_norm_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    existing = {
        r[0] for r in conn.execute(sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'user_collection'"
        ))
    }

    if "card_id" not in existing:
        op.add_column("user_collection", sa.Column("card_id", sa.Integer(), nullable=True))
    if "printing_id" not in existing:
        op.add_column("user_collection", sa.Column("printing_id", sa.Integer(), nullable=True))

    # 1. Impression exacte quand l'identifiant Scryfall est connu
    op.execute("""
        UPDATE user_collection uc
        SET printing_id = p.id, card_id = p.card_id
        FROM scryfall_card_printings p
        WHERE uc.scryfall_id IS NOT NULL
          AND p.scryfall_id = uc.scryfall_id
    """)

    # 2. Sinon, la carte par son nom normalise (sans impression precise)
    op.execute("""
        UPDATE user_collection uc
        SET card_id = (
            SELECT sc.id
            FROM scryfall_cards sc
            WHERE sc.normalized_name = mm_normalize_name(uc.card_name)
            ORDER BY (sc.type_line NOT ILIKE '%Token%') DESC, sc.id
            LIMIT 1
        )
        WHERE uc.card_id IS NULL
    """)

    # 3. Enfin, les listes ne citent parfois que la face avant d'une carte
    #    recto-verso ("Heliod, the Radiant Dawn" pour "... // Heliod, the Warped
    #    Eclipse") : on rattrape ces cas par la premiere face.
    op.execute("""
        UPDATE user_collection uc
        SET card_id = (
            SELECT sc.id
            FROM scryfall_cards sc
            WHERE split_part(sc.normalized_name, ' // ', 1) = mm_normalize_name(uc.card_name)
            ORDER BY (sc.type_line NOT ILIKE '%Token%') DESC, sc.id
            LIMIT 1
        )
        WHERE uc.card_id IS NULL
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_user_collection_card_id
        ON user_collection (card_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_user_collection_printing_id
        ON user_collection (printing_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_user_collection_card_id")
    op.execute("DROP INDEX IF EXISTS ix_user_collection_printing_id")
    op.drop_column("user_collection", "printing_id")
    op.drop_column("user_collection", "card_id")
