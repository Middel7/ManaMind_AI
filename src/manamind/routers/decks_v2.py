"""Router des decks — vue enrichie.

Complete /api/moxfield-decks en joignant en une seule requete l'illustration du
commandant, le nombre de cartes, la valeur et la part deja possedee. Les pages
n'ont ainsi pas a interroger l'API carte par carte.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from sqlalchemy import text

from manamind.auth import COOKIE_NAME, get_current_user
from manamind.db.engine import SessionLocal

from ._shared import _json_response

router = APIRouter()

# Impression illustrant une carte : on ecarte les promos, moins representatives.
_ART_SQL = """
    LEFT JOIN LATERAL (
        SELECT p.scryfall_id, p.image_small, p.image_normal, p.rarity,
               UPPER(p.set_code) AS set_code, p.collector_number
        FROM scryfall_card_printings p
        JOIN scryfall_cards c ON c.id = p.card_id
        LEFT JOIN scryfall_mtg_sets ms ON LOWER(ms.code) = LOWER(p.set_code)
        WHERE c.normalized_name = mm_normalize_name({name_expr})
          AND p.lang = 'en'
        ORDER BY (p.image_normal IS NOT NULL) DESC,
                 (p.promo IS NOT TRUE) DESC,
                 (COALESCE(ms.set_type, '') NOT IN ('promo', 'memorabilia')) DESC,
                 p.released_at DESC NULLS LAST
        LIMIT 1
    ) art ON TRUE
"""

# Sans edition connue, la valeur de reference est l'impression la moins chere :
# celle de l'impression illustrative surevaluerait les cartes reimprimees en premium.
_PRICE_SQL = """
    LEFT JOIN LATERAL (
        SELECT MIN(latest.trend_price) AS unit_price
        FROM scryfall_cards c2
        JOIN scryfall_card_printings p2 ON p2.card_id = c2.id
        CROSS JOIN LATERAL (
            SELECT pge.trend_price
            FROM cardmarket_price_guide_entries pge
            WHERE pge.id_product = p2.cardmarket_id
            ORDER BY pge.captured_at DESC
            LIMIT 1
        ) latest
        WHERE c2.normalized_name = mm_normalize_name({name_expr})
          AND p2.cardmarket_id IS NOT NULL
          AND latest.trend_price > 0
    ) price ON TRUE
"""


def _user(request: Request) -> dict:
    return get_current_user(mm_token=request.cookies.get(COOKIE_NAME))


@router.get("/api/v2/decks")
def api_decks(request: Request) -> Response:
    """Decks de l'utilisateur, avec illustration et taux de possession."""
    user = _user(request)

    with SessionLocal() as session:
        rows = session.execute(text(f"""
            SELECT d.deck_id, d.name, d.commander, d.moxfield_url,
                   COALESCE(d.fetched_at, d.created_at) AS updated_at,
                   d.locally_modified,
                   COALESCE(agg.cards, 0)  AS card_count,
                   COALESCE(agg.owned, 0)  AS owned_count,
                   art.scryfall_id, art.image_small, art.image_normal
            FROM user_moxfield_decks d
            LEFT JOIN LATERAL (
                SELECT SUM(dc.quantity) AS cards,
                       SUM(dc.quantity) FILTER (
                           WHERE EXISTS (
                               SELECT 1 FROM user_collection uc
                               WHERE uc.user_id = d.user_id
                                 AND mm_normalize_name(uc.card_name) = mm_normalize_name(dc.card_name)
                           )
                       ) AS owned
                FROM user_deck_cards dc
                WHERE dc.user_id = d.user_id
                  AND mm_normalize_name(dc.commander) = mm_normalize_name(d.commander)
            ) agg ON TRUE
            {_ART_SQL.format(name_expr="split_part(d.commander, '//', 1)")}
            WHERE d.user_id = :uid
            ORDER BY COALESCE(d.fetched_at, d.created_at) DESC NULLS LAST
        """), {"uid": user["id"]}).fetchall()

    decks = []
    for row in rows:
        cards = int(row.card_count or 0)
        owned = int(row.owned_count or 0)
        decks.append({
            "deck_id": row.deck_id,
            "name": row.name or row.commander,
            "commander": row.commander,
            "url": row.moxfield_url,
            "card_count": cards,
            "owned_count": owned,
            "owned_ratio": round(owned / cards, 3) if cards else 0,
            "locally_modified": bool(row.locally_modified),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "scryfall_id": row.scryfall_id,
            "image_small": row.image_small,
            "image_normal": row.image_normal,
        })
    return _json_response({"decks": decks})


@router.get("/api/v2/decks/{deck_id}")
def api_deck_detail(deck_id: str, request: Request) -> Response:
    """Cartes d'un deck, avec illustration, prix et possession."""
    user = _user(request)

    with SessionLocal() as session:
        deck = session.execute(text("""
            SELECT deck_id, name, commander, moxfield_url, locally_modified,
                   COALESCE(fetched_at, created_at) AS updated_at
            FROM user_moxfield_decks
            WHERE user_id = :uid AND deck_id = :did
        """), {"uid": user["id"], "did": deck_id}).fetchone()

        if deck is None:
            return _json_response({"error": "Deck introuvable"}, status_code=404)

        rows = session.execute(text(f"""
            SELECT dc.card_name, dc.quantity,
                   sc.type_line, sc.mana_cost, sc.mana_value,
                   sc.color_identity, sc.oracle_text,
                   COALESCE(sc.game_changer, false) AS game_changer,
                   art.scryfall_id, art.image_small, art.image_normal,
                   art.rarity, art.set_code, art.collector_number,
                   COALESCE(owned.qty, 0) AS owned,
                   price.unit_price
            FROM user_deck_cards dc
            LEFT JOIN LATERAL (
                SELECT c.type_line, c.mana_cost, c.mana_value, c.color_identity,
                       c.oracle_text, c.game_changer, c.normalized_name
                FROM scryfall_cards c
                WHERE c.normalized_name = mm_normalize_name(dc.card_name)
                ORDER BY (c.type_line NOT ILIKE '%Token%') DESC, c.id
                LIMIT 1
            ) sc ON TRUE
            {_ART_SQL.format(name_expr="dc.card_name")}
            {_PRICE_SQL.format(name_expr="dc.card_name")}
            LEFT JOIN LATERAL (
                SELECT SUM(uc.quantity) AS qty
                FROM user_collection uc
                WHERE uc.user_id = :uid
                  AND mm_normalize_name(uc.card_name) = mm_normalize_name(dc.card_name)
            ) owned ON TRUE
            WHERE dc.user_id = :uid
              AND mm_normalize_name(dc.commander) = mm_normalize_name(:commander)
            ORDER BY dc.card_name
        """), {"uid": user["id"], "commander": deck.commander}).fetchall()

    cards = []
    total_value = 0.0
    owned_copies = 0
    total_copies = 0
    for row in rows:
        price = float(row.unit_price) if row.unit_price is not None else None
        quantity = int(row.quantity or 1)
        total_copies += quantity
        owned_copies += min(int(row.owned or 0), quantity)
        if price:
            total_value += price * quantity
        cards.append({
            "card_name": row.card_name,
            "quantity": quantity,
            "owned": int(row.owned or 0),
            "type_line": row.type_line or "",
            "mana_cost": row.mana_cost or "",
            "mana_value": row.mana_value,
            "color_identity": list(row.color_identity or []),
            "oracle_text": row.oracle_text or "",
            "game_changer": bool(row.game_changer),
            "scryfall_id": row.scryfall_id,
            "image_small": row.image_small,
            "image_normal": row.image_normal,
            "rarity": row.rarity,
            "set_code": row.set_code,
            "collector_number": row.collector_number,
            "unit_price": price,
        })

    return _json_response({
        "deck": {
            "deck_id": deck.deck_id,
            "name": deck.name or deck.commander,
            "commander": deck.commander,
            "url": deck.moxfield_url,
            "locally_modified": bool(deck.locally_modified),
            "updated_at": deck.updated_at.isoformat() if deck.updated_at else None,
            "card_count": total_copies,
            "owned_count": owned_copies,
            "owned_ratio": round(owned_copies / total_copies, 3) if total_copies else 0,
            "value_eur": round(total_value, 2),
        },
        "cards": cards,
    })


@router.get("/api/v2/decks/{deck_id}/missing")
def api_deck_missing(
    deck_id: str,
    request: Request,
    limit: int = Query(60, ge=1, le=200),
) -> Response:
    """Cartes du deck absentes de la collection, les plus cheres d'abord."""
    user = _user(request)

    with SessionLocal() as session:
        commander = session.execute(text(
            "SELECT commander FROM user_moxfield_decks WHERE user_id = :uid AND deck_id = :did"
        ), {"uid": user["id"], "did": deck_id}).scalar()
        if commander is None:
            return _json_response({"error": "Deck introuvable"}, status_code=404)

        rows = session.execute(text(f"""
            SELECT dc.card_name, dc.quantity,
                   art.scryfall_id, art.image_small, art.image_normal,
                   art.rarity, art.set_code,
                   price.unit_price
            FROM user_deck_cards dc
            {_ART_SQL.format(name_expr="dc.card_name")}
            {_PRICE_SQL.format(name_expr="dc.card_name")}
            WHERE dc.user_id = :uid
              AND mm_normalize_name(dc.commander) = mm_normalize_name(:commander)
              AND NOT EXISTS (
                  SELECT 1 FROM user_collection uc
                  WHERE uc.user_id = :uid
                    AND mm_normalize_name(uc.card_name) = mm_normalize_name(dc.card_name)
              )
            ORDER BY price.unit_price DESC NULLS LAST
            LIMIT :limit
        """), {"uid": user["id"], "commander": commander, "limit": limit}).fetchall()

    missing = [
        {
            "card_name": r.card_name,
            "quantity": int(r.quantity or 1),
            "scryfall_id": r.scryfall_id,
            "image_small": r.image_small,
            "image_normal": r.image_normal,
            "rarity": r.rarity,
            "set_code": r.set_code,
            "unit_price": float(r.unit_price) if r.unit_price is not None else None,
        }
        for r in rows
    ]
    total = sum(item["unit_price"] * item["quantity"]
                for item in missing if item["unit_price"])
    return _json_response({"missing": missing, "total_eur": round(total, 2)})
