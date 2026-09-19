"""Rattacher les cartes a leur deck par identifiant, plus par nom de commandant

Revision ID: 20260904_deck_cards_id
Revises: 20260904_card_min_price
Create Date: 2026-09-04

user_deck_cards n'avait pas de deck_id : une carte etait reliee a son deck par
le nom du commandant, avec une unicite (user_id, commander, card_name). Deux
consequences :

- deux decks du meme commandant partageaient physiquement la meme liste, ce qui
  interdisait d'en avoir deux (budget et optimise, par exemple) ;
- toutes les lectures joignaient par mm_normalize_name(commander) des deux
  cotes, ce qui interdit le hash join et coutait cher.

La colonne deck_id est ajoutee, remplie depuis le commandant — sans perte, les
23 decks ayant chacun un commandant distinct au moment de la migration — puis
l'unicite bascule sur (user_id, deck_id, card_name).

Les tables appartiennent au role postgres : l'ALTER TABLE demande une connexion
privilegiee, prise dans POSTGRES_URL. Sans cette variable, la migration
s'arrete avec un message explicite plutot que de laisser le schema a moitie
modifie.
"""

from __future__ import annotations

import os

from alembic import op
from sqlalchemy import create_engine, text

revision = "20260904_deck_cards_id"
down_revision = "20260904_card_min_price"
branch_labels = None
depends_on = None


def _privileged():
    """Connexion propriétaire des tables user_*, ou None."""
    url = os.environ.get("POSTGRES_URL", "")
    if not url:
        return None
    return create_engine(url)


def upgrade() -> None:
    engine = _privileged()
    if engine is None:
        raise RuntimeError(
            "POSTGRES_URL est requis : user_deck_cards appartient au role postgres "
            "et l'utilisateur applicatif ne peut pas la modifier."
        )

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE user_deck_cards ADD COLUMN IF NOT EXISTS deck_id TEXT"))

        # Report depuis le commandant, seul lien existant entre les deux tables.
        conn.execute(text("""
            UPDATE user_deck_cards dc
            SET deck_id = d.deck_id
            FROM user_moxfield_decks d
            WHERE d.user_id = dc.user_id
              AND mm_normalize_name(d.commander) = mm_normalize_name(dc.commander)
              AND dc.deck_id IS NULL
        """))

        # Une carte sans deck correspondant n'a plus de sens : on la rattache a
        # un deck cree pour l'occasion plutot que de la perdre silencieusement.
        orphelines = conn.execute(text("""
            SELECT DISTINCT user_id, commander FROM user_deck_cards WHERE deck_id IS NULL
        """)).fetchall()
        for row in orphelines:
            deck_id = f"recovered-{abs(hash((row.user_id, row.commander))) % 10**10:010d}"
            conn.execute(text("""
                INSERT INTO user_moxfield_decks
                       (user_id, deck_id, moxfield_url, commander, name, locally_modified)
                VALUES (:uid, :did, '', :cmd, :cmd, TRUE)
                ON CONFLICT (user_id, deck_id) DO NOTHING
            """), {"uid": row.user_id, "did": deck_id, "cmd": row.commander})
            conn.execute(text("""
                UPDATE user_deck_cards SET deck_id = :did
                WHERE user_id = :uid AND commander = :cmd AND deck_id IS NULL
            """), {"uid": row.user_id, "did": deck_id, "cmd": row.commander})

        conn.execute(text("ALTER TABLE user_deck_cards ALTER COLUMN deck_id SET NOT NULL"))
        conn.execute(text("""
            ALTER TABLE user_deck_cards
            DROP CONSTRAINT IF EXISTS uq_user_deck_card
        """))
        conn.execute(text("""
            ALTER TABLE user_deck_cards
            ADD CONSTRAINT uq_user_deck_card UNIQUE (user_id, deck_id, card_name)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_user_deck_cards_deck
            ON user_deck_cards (user_id, deck_id)
        """))
        # Les lectures passent desormais par deck_id : l'application doit
        # pouvoir ecrire la colonne.
        conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON user_deck_cards TO manamind"))
    engine.dispose()


def downgrade() -> None:
    engine = _privileged()
    if engine is None:
        raise RuntimeError("POSTGRES_URL est requis pour revenir en arriere.")
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_user_deck_cards_deck"))
        conn.execute(text("ALTER TABLE user_deck_cards DROP CONSTRAINT IF EXISTS uq_user_deck_card"))
        conn.execute(text("""
            ALTER TABLE user_deck_cards
            ADD CONSTRAINT uq_user_deck_card UNIQUE (user_id, commander, card_name)
        """))
        conn.execute(text("ALTER TABLE user_deck_cards DROP COLUMN IF EXISTS deck_id"))
    engine.dispose()
