"""collection v2 : exemplaires enrichis + profils utilisateur

Passe user_collection d'un modele (nom, quantite) a un modele par exemplaire :
edition, numero de collecteur, finition, langue, etat, rangement, dates.

Les donnees existantes portent deja l'edition dans raw_line
(ex. "1 Hope Estheim (FIN) 226" ou "1 Angel of Vitality (FDN) 706 *F*"),
elle est donc retro-remplie plutot que perdue.

Revision ID: 20260902_collection_v2
Revises: 20260901_cmd_swap_idx
Create Date: 2026-09-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_collection_v2"
down_revision: Union[str, None] = "20260901_cmd_swap_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS = [
    ("set_code", sa.Text(), None),
    ("collector_number", sa.Text(), None),
    ("finish", sa.Text(), "'nonfoil'"),
    ("language", sa.Text(), "'en'"),
    ("condition", sa.Text(), None),
    ("location", sa.Text(), None),
    ("scryfall_id", sa.Text(), None),
    ("note", sa.Text(), None),
]


def upgrade() -> None:
    conn = op.get_bind()

    # -- 1. Colonnes d'exemplaire ---------------------------------------------
    existing = {
        r[0] for r in conn.execute(sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'user_collection'"
        ))
    }
    for name, type_, default in _NEW_COLUMNS:
        if name not in existing:
            op.add_column(
                "user_collection",
                sa.Column(
                    name, type_, nullable=True,
                    server_default=sa.text(default) if default else None,
                ),
            )

    # Le server_default remplit deja NOW() sur les lignes existantes : on les
    # recale sur leur date d'import reelle, sinon tout l'historique est ecrase.
    if "added_at" not in existing:
        op.add_column("user_collection", sa.Column(
            "added_at", sa.DateTime(timezone=True), nullable=True,
            server_default=sa.text("NOW()")))
        op.execute(
            "UPDATE user_collection SET added_at = imported_at "
            "WHERE imported_at IS NOT NULL"
        )
    if "updated_at" not in existing:
        op.add_column("user_collection", sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=True,
            server_default=sa.text("NOW()")))
        op.execute(
            "UPDATE user_collection SET updated_at = imported_at "
            "WHERE imported_at IS NOT NULL"
        )

    # -- 2. Retro-remplissage depuis raw_line ---------------------------------
    # "1 Together as One (SOS) #4" / "1 Hope Estheim (FIN) 226" / "... *F*"
    op.execute(r"""
        UPDATE user_collection
        SET set_code = UPPER(substring(raw_line from '\(([A-Za-z0-9]{2,6})\)'))
        WHERE set_code IS NULL
          AND raw_line ~ '\([A-Za-z0-9]{2,6}\)'
    """)
    op.execute(r"""
        UPDATE user_collection
        SET collector_number = substring(
            raw_line from '\([A-Za-z0-9]{2,6}\)\s*#?\s*([A-Za-z0-9][A-Za-z0-9-]*)')
        WHERE collector_number IS NULL
          AND raw_line ~ '\([A-Za-z0-9]{2,6}\)\s*#?\s*[A-Za-z0-9]'
    """)
    op.execute(
        "UPDATE user_collection SET finish = 'foil' WHERE raw_line LIKE '%*F*%'"
    )
    op.execute(
        "UPDATE user_collection SET finish = 'etched' WHERE raw_line LIKE '%*E*%'"
    )
    op.execute("UPDATE user_collection SET finish   = 'nonfoil' WHERE finish   IS NULL")
    op.execute("UPDATE user_collection SET language = 'en'      WHERE language IS NULL")

    # Rattacher l'impression Scryfall quand edition + numero l'identifient
    op.execute("""
        UPDATE user_collection uc
        SET scryfall_id = p.scryfall_id
        FROM scryfall_card_printings p
        WHERE uc.scryfall_id IS NULL
          AND uc.set_code IS NOT NULL
          AND uc.collector_number IS NOT NULL
          AND UPPER(p.set_code) = uc.set_code
          AND p.collector_number = uc.collector_number
          AND p.lang = 'en'
    """)

    # -- 3. Fusionner les exemplaires devenus identiques ----------------------
    op.execute("DELETE FROM user_collection WHERE user_id IS NULL")
    op.execute("""
        WITH grouped AS (
            SELECT MIN(id) AS keep_id, SUM(quantity) AS total
            FROM user_collection
            GROUP BY user_id, LOWER(TRIM(card_name)),
                     COALESCE(set_code, ''), COALESCE(collector_number, ''),
                     finish, language, COALESCE(condition, '')
            HAVING COUNT(*) > 1
        )
        UPDATE user_collection uc
        SET quantity = g.total
        FROM grouped g
        WHERE uc.id = g.keep_id
    """)
    op.execute("""
        DELETE FROM user_collection uc
        USING (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY user_id, LOWER(TRIM(card_name)),
                                    COALESCE(set_code, ''),
                                    COALESCE(collector_number, ''),
                                    finish, language, COALESCE(condition, '')
                       ORDER BY id
                   ) AS rn
            FROM user_collection
        ) d
        WHERE uc.id = d.id AND d.rn > 1
    """)

    # -- 4. Contraintes et index ----------------------------------------------
    op.alter_column("user_collection", "user_id", nullable=False)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_user_collection_item
        ON user_collection (
            user_id, LOWER(TRIM(card_name)),
            COALESCE(set_code, ''), COALESCE(collector_number, ''),
            finish, language, COALESCE(condition, '')
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_user_collection_user_name
        ON user_collection (user_id, LOWER(TRIM(card_name)))
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_user_collection_added_at
        ON user_collection (user_id, added_at DESC)
    """)

    # -- 5. Profils ------------------------------------------------------------
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("avatar_scryfall_id", sa.Text(), nullable=True),
        sa.Column("avatar_card_name", sa.Text(), nullable=True),
        sa.Column("banner_scryfall_id", sa.Text(), nullable=True),
        sa.Column("banner_card_name", sa.Text(), nullable=True),
        sa.Column("favorite_commander", sa.Text(), nullable=True),
        sa.Column("collection_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("onboarding_dismissed", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("user_profiles")
    op.execute("DROP INDEX IF EXISTS uq_user_collection_item")
    op.execute("DROP INDEX IF EXISTS ix_user_collection_user_name")
    op.execute("DROP INDEX IF EXISTS ix_user_collection_added_at")
    op.alter_column("user_collection", "user_id", nullable=True)
    for name, _type, _default in _NEW_COLUMNS:
        op.drop_column("user_collection", name)
    op.drop_column("user_collection", "added_at")
    op.drop_column("user_collection", "updated_at")
