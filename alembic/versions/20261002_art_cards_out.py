"""Cartes d'art rattachees a la vraie carte

Revision ID: 20261002_art_cards_out
Revises: 20261002_price_trend
Create Date: 2026-10-02

Les cartes d'art (Art Series : ALTR, AFIN, AMOM…, type « Card // Card ») ne
sont pas des cartes jouables et n'ont plus cours dans le projet. Des imports
anterieurs en avaient pourtant rattache a la collection — une ligne
« Minas Tirith (ALTR) 12 » visait la carte d'art — et deux decks en citaient
le nom double (« Plains // Plains »).

Chaque exemplaire passe sur la vraie carte, dans l'edition que les ecrans
retiennent par defaut. Si la collection detient deja cette edition dans la
meme finition, langue et condition, les quantites sont fusionnees : l'index
unique de user_collection interdit deux lignes identiques. Meme regle pour
les lignes de deck, dont le nom est ramene a sa face avant.

Pas de retour arriere : la carte d'art d'origine n'est pas conservee, et
n'aurait de toute facon plus de lecteur.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261002_art_cards_out"
down_revision = "20261002_price_trend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(sa.text("""
        SELECT uc.id, uc.user_id, uc.card_name, uc.quantity, uc.finish,
               uc.language, uc.condition,
               split_part(art.normalized_name, ' // ', 1) AS front
        FROM user_collection uc
        JOIN scryfall_cards art ON art.id = uc.card_id
        WHERE art.type_line LIKE 'Card%'
    """)).fetchall()

    for row in rows:
        real = bind.execute(sa.text("""
            SELECT c.id FROM scryfall_cards c
            WHERE (c.normalized_name = :front
                   OR split_part(c.normalized_name, ' // ', 1) = :front)
              AND c.type_line NOT LIKE 'Card%'
            ORDER BY (c.normalized_name = :front) DESC,
                     (EXISTS (SELECT 1 FROM scryfall_card_printings q
                              WHERE q.card_id = c.id AND q.digital IS NOT TRUE)) DESC,
                     (c.type_line NOT ILIKE '%Token%') DESC, c.id
            LIMIT 1
        """), {"front": row.front}).scalar()
        if real is None:
            continue

        printing = bind.execute(sa.text("""
            SELECT p.id, p.scryfall_id, UPPER(p.set_code) AS set_code,
                   p.collector_number
            FROM scryfall_card_printings p
            WHERE p.card_id = :cid AND p.lang = 'en'
            ORDER BY (p.set_code NOT ILIKE 'sl%'
                      AND LOWER(p.set_code) NOT IN ('mar', 'lmar', 'pza')) DESC,
                     (p.digital IS NOT TRUE) DESC,
                     (p.image_normal IS NOT NULL) DESC,
                     (p.promo IS NOT TRUE) DESC,
                     p.released_at DESC NULLS LAST
            LIMIT 1
        """), {"cid": real}).fetchone()

        set_code = printing.set_code if printing else None
        number = printing.collector_number if printing else None

        twin = bind.execute(sa.text("""
            SELECT id FROM user_collection
            WHERE user_id = :uid AND id <> :id
              AND LOWER(TRIM(card_name)) = LOWER(TRIM(:name))
              AND COALESCE(set_code, '') = COALESCE(:set, '')
              AND COALESCE(collector_number, '') = COALESCE(:num, '')
              AND finish = :finish AND language = :lang
              AND COALESCE(condition, '') = COALESCE(:cond, '')
        """), {"uid": row.user_id, "id": row.id, "name": row.card_name,
               "set": set_code, "num": number, "finish": row.finish,
               "lang": row.language, "cond": row.condition}).scalar()

        if twin is not None:
            bind.execute(sa.text("""
                UPDATE user_collection SET quantity = quantity + :qty,
                       updated_at = NOW()
                WHERE id = :twin
            """), {"qty": row.quantity, "twin": twin})
            bind.execute(sa.text("DELETE FROM user_collection WHERE id = :id"),
                         {"id": row.id})
        else:
            bind.execute(sa.text("""
                UPDATE user_collection
                SET card_id = :cid,
                    printing_id = :pid,
                    scryfall_id = :sid,
                    set_code = :set,
                    collector_number = :num,
                    updated_at = NOW()
                WHERE id = :id
            """), {"cid": real, "pid": printing.id if printing else None,
                   "sid": printing.scryfall_id if printing else None,
                   "set": set_code, "num": number, "id": row.id})

    # Lignes de deck au nom d'une carte d'art : ramenees a la face avant.
    deck_rows = bind.execute(sa.text("""
        SELECT dc.id, dc.user_id, dc.deck_id, dc.quantity,
               split_part(dc.card_name, ' // ', 1) AS front
        FROM user_deck_cards dc
        JOIN scryfall_cards art
          ON art.normalized_name = mm_normalize_name(dc.card_name)
        WHERE art.type_line LIKE 'Card%'
    """)).fetchall()

    for row in deck_rows:
        twin = bind.execute(sa.text("""
            SELECT id FROM user_deck_cards
            WHERE user_id = :uid AND deck_id = :did AND id <> :id
              AND card_name = :name
        """), {"uid": row.user_id, "did": row.deck_id, "id": row.id,
               "name": row.front}).scalar()
        if twin is not None:
            bind.execute(sa.text("""
                UPDATE user_deck_cards SET quantity = quantity + :qty WHERE id = :twin
            """), {"qty": row.quantity, "twin": twin})
            bind.execute(sa.text("DELETE FROM user_deck_cards WHERE id = :id"),
                         {"id": row.id})
        else:
            bind.execute(sa.text("""
                UPDATE user_deck_cards SET card_name = :name WHERE id = :id
            """), {"name": row.front, "id": row.id})


def downgrade() -> None:
    # Le rattachement a la carte d'art n'est pas restaure : voir l'en-tete.
    pass
