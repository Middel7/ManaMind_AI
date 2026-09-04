"""Router des decks — vue enrichie.

Complete /api/moxfield-decks en joignant en une seule requete l'illustration du
commandant, le nombre de cartes, la valeur et la part deja possedee. Les pages
n'ont ainsi pas a interroger l'API carte par carte.
"""

from __future__ import annotations

import uuid

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

# Prix de reference du projet : le low_price Cardmarket de l'edition la moins
# chere. L'impression illustrative surevaluerait les cartes reimprimees en
# premium, et la tendance depasse presque toujours la meilleure offre.
_PRICE_SQL = """
    LEFT JOIN LATERAL (
        SELECT MIN(cmp.low_price) AS unit_price
        FROM scryfall_cards c2
        JOIN card_min_price cmp ON cmp.card_id = c2.id
        WHERE c2.normalized_name = mm_normalize_name({name_expr})
    ) price ON TRUE
"""


# Noms possedes, normalises et agreges une seule fois. Correlee carte par carte,
# la meme condition relancait un parcours complet de user_collection pour chaque
# ligne de deck (~2 s sur 20 decks) : le CTE la ramene a un unique parcours.
_OWNED_SQL = """
    SELECT split_part(mm_normalize_name(uc.card_name), ' // ', 1) AS key,
           SUM(uc.quantity) AS qty
    FROM user_collection uc
    WHERE uc.user_id = :uid
    GROUP BY 1
"""


def _user(request: Request) -> dict:
    return get_current_user(mm_token=request.cookies.get(COOKIE_NAME))


@router.get("/api/v2/decks")
def api_decks(request: Request) -> Response:
    """Decks de l'utilisateur, avec illustration et taux de possession."""
    user = _user(request)

    with SessionLocal() as session:
        rows = session.execute(text(f"""
            WITH owned_idx AS ({_OWNED_SQL}),
            -- Un seul parcours de user_deck_cards, groupe par commandant :
            -- le LATERAL correle en refaisait un par deck.
            agg AS (
                SELECT dc.deck_id,
                       SUM(dc.quantity) AS cards,
                       SUM(dc.quantity) FILTER (WHERE o.key IS NOT NULL) AS owned
                FROM user_deck_cards dc
                LEFT JOIN owned_idx o
                  ON o.key = split_part(mm_normalize_name(dc.card_name), ' // ', 1)
                WHERE dc.user_id = :uid
                GROUP BY 1
            )
            SELECT d.deck_id, d.name, d.commander, d.moxfield_url,
                   COALESCE(d.fetched_at, d.created_at) AS updated_at,
                   d.locally_modified,
                   COALESCE(agg.cards, 0)  AS card_count,
                   COALESCE(agg.owned, 0)  AS owned_count,
                   art.scryfall_id, art.image_small, art.image_normal
            FROM user_moxfield_decks d
            LEFT JOIN agg ON agg.deck_id = d.deck_id
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


@router.post("/api/v2/decks")
async def api_create_deck(request: Request) -> Response:
    """Cree un deck vide autour d'un commandant.

    Les cartes etant rattachees au deck par le nom de son commandant, deux
    decks ne peuvent pas partager le meme : leurs listes se confondraient.
    """
    user = _user(request)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Corps JSON invalide"}, status_code=400)

    commander = (body.get("commander") or "").strip()
    name = (body.get("name") or "").strip() or commander
    if not commander:
        return _json_response({"error": "Commandant manquant"}, status_code=400)

    with SessionLocal() as session:
        # L'identite de couleur du commandant borne ce que le deck peut jouer.
        identity = session.execute(text("""
            SELECT color_identity FROM scryfall_cards
            WHERE normalized_name = mm_normalize_name(:cmd)
            ORDER BY id
            LIMIT 1
        """), {"cmd": commander}).scalar()

        deck_id = f"new-{uuid.uuid4().hex[:12]}"
        session.execute(text("""
            INSERT INTO user_moxfield_decks
                   (user_id, deck_id, moxfield_url, commander, name, locally_modified)
            VALUES (:uid, :did, '', :cmd, :name, TRUE)
        """), {"uid": user["id"], "did": deck_id, "cmd": commander, "name": name})
        session.commit()

    return _json_response({
        "deck_id": deck_id,
        "commander": commander,
        "name": name,
        "color_identity": list(identity or []),
    })


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
            WITH owned_idx AS ({_OWNED_SQL}),
            -- Exemplaires engages dans un deck, pour dire ce qui reste libre.
            deck_use AS (
                SELECT split_part(mm_normalize_name(dc.card_name), ' // ', 1) AS key,
                       count(DISTINCT dc.deck_id) AS decks
                FROM user_deck_cards dc
                WHERE dc.user_id = :uid
                GROUP BY 1
            )
            SELECT dc.card_name, dc.quantity,
                   sc.type_line, sc.mana_cost, sc.mana_value,
                   sc.color_identity, sc.oracle_text,
                   COALESCE(sc.game_changer, false) AS game_changer,
                   art.scryfall_id, art.image_small, art.image_normal,
                   art.rarity, art.set_code, art.collector_number,
                   COALESCE(owned.qty, 0) AS owned,
                   COALESCE(du.decks, 0) AS decks_used,
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
            LEFT JOIN owned_idx owned
              ON owned.key = split_part(mm_normalize_name(dc.card_name), ' // ', 1)
            LEFT JOIN deck_use du
              ON du.key = split_part(mm_normalize_name(dc.card_name), ' // ', 1)
            WHERE dc.user_id = :uid AND dc.deck_id = :did
            ORDER BY dc.card_name
        """), {"uid": user["id"], "did": deck_id}).fetchall()

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
            "used": int(row.decks_used or 0),
            "free": max(0, int(row.owned or 0) - int(row.decks_used or 0)),
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


@router.post("/api/v2/decks/{deck_id}/commander")
async def api_set_commander(deck_id: str, request: Request) -> Response:
    """Designe une carte du deck comme son commandant.

    Utile quand l'import n'a pas su l'identifier : le deck est alors enregistre
    sous « Unknown ». Les cartes etant rattachees au deck par le nom de son
    commandant, les deux tables doivent changer ensemble, d'ou la transaction.
    """
    user = _user(request)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Corps JSON invalide"}, status_code=400)

    card_name = (body.get("card_name") or "").strip()
    if not card_name:
        return _json_response({"error": "Nom de carte manquant"}, status_code=400)

    with SessionLocal() as session:
        current = session.execute(text("""
            SELECT commander FROM user_moxfield_decks
            WHERE user_id = :uid AND deck_id = :did
        """), {"uid": user["id"], "did": deck_id}).scalar()
        if current is None:
            return _json_response({"error": "Deck introuvable"}, status_code=404)

        in_deck = session.execute(text("""
            SELECT 1 FROM user_deck_cards
            WHERE user_id = :uid AND deck_id = :did
              AND mm_normalize_name(card_name) = mm_normalize_name(:card)
        """), {"uid": user["id"], "did": deck_id, "card": card_name}).scalar()
        if not in_deck:
            return _json_response(
                {"error": "Cette carte ne fait pas partie du deck"}, status_code=400)

        # Les SELECT ci-dessus ont deja ouvert la transaction : les deux UPDATE
        # y prennent place et sont valides ensemble par le commit final.
        # Le commandant reste ecrit sur les cartes pour les analyses qui
        # raisonnent par commandant, mais il ne les rattache plus au deck.
        session.execute(text("""
            UPDATE user_deck_cards SET commander = :new
            WHERE user_id = :uid AND deck_id = :did
        """), {"uid": user["id"], "did": deck_id, "new": card_name})
        session.execute(text("""
            UPDATE user_moxfield_decks SET commander = :new, locally_modified = TRUE
            WHERE user_id = :uid AND deck_id = :did
        """), {"uid": user["id"], "did": deck_id, "new": card_name})
        session.commit()

    return _json_response({"ok": True, "commander": card_name})


@router.get("/api/v2/hidden-moves")
def api_hidden_moves(request: Request) -> Response:
    """Deplacements que l'utilisateur a ecartes, les plus recents d'abord."""
    user = _user(request)
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT id, card_name, from_commander, to_commander, hidden_at
            FROM user_hidden_moves
            WHERE user_id = :uid
            ORDER BY hidden_at DESC, id DESC
        """), {"uid": user["id"]}).fetchall()

    return _json_response({"moves": [
        {
            "id": r.id,
            "card_name": r.card_name,
            "from_commander": r.from_commander,
            "to_commander": r.to_commander,
            "hidden_at": r.hidden_at.isoformat() if r.hidden_at else None,
        }
        for r in rows
    ]})


@router.post("/api/v2/hidden-moves")
async def api_hide_move(request: Request) -> Response:
    """Ecarte un deplacement : il ne sera plus propose."""
    user = _user(request)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Corps JSON invalide"}, status_code=400)

    card = (body.get("card_name") or "").strip()
    origin = (body.get("from_commander") or "").strip()
    target = (body.get("to_commander") or "").strip()
    if not (card and origin and target):
        return _json_response(
            {"error": "card_name, from_commander et to_commander sont requis"},
            status_code=400)

    with SessionLocal() as session:
        session.execute(text("""
            INSERT INTO user_hidden_moves (user_id, card_name, from_commander, to_commander)
            VALUES (:uid, :card, :origin, :target)
            ON CONFLICT (user_id, card_name, from_commander, to_commander) DO NOTHING
        """), {"uid": user["id"], "card": card, "origin": origin, "target": target})
        session.commit()

    return _json_response({"ok": True})


@router.delete("/api/v2/hidden-moves/{move_id}")
def api_restore_move(move_id: int, request: Request) -> Response:
    """Remet un deplacement dans les suggestions."""
    user = _user(request)
    with SessionLocal() as session:
        result = session.execute(text("""
            DELETE FROM user_hidden_moves WHERE id = :mid AND user_id = :uid
        """), {"mid": move_id, "uid": user["id"]})
        session.commit()
    if not result.rowcount:
        return _json_response({"error": "Suggestion introuvable"}, status_code=404)
    return _json_response({"ok": True})


@router.get("/api/v2/decks/{deck_id}/missing")
def api_deck_missing(
    deck_id: str,
    request: Request,
    limit: int = Query(60, ge=1, le=200),
) -> Response:
    """Cartes du deck absentes de la collection, les plus cheres d'abord."""
    user = _user(request)

    with SessionLocal() as session:
        exists = session.execute(text(
            "SELECT 1 FROM user_moxfield_decks WHERE user_id = :uid AND deck_id = :did"
        ), {"uid": user["id"], "did": deck_id}).scalar()
        if not exists:
            return _json_response({"error": "Deck introuvable"}, status_code=404)

        rows = session.execute(text(f"""
            SELECT dc.card_name, dc.quantity,
                   art.scryfall_id, art.image_small, art.image_normal,
                   art.rarity, art.set_code,
                   price.unit_price
            FROM user_deck_cards dc
            {_ART_SQL.format(name_expr="dc.card_name")}
            {_PRICE_SQL.format(name_expr="dc.card_name")}
            WHERE dc.user_id = :uid AND dc.deck_id = :did
              AND NOT EXISTS (
                  SELECT 1 FROM user_collection uc
                  WHERE uc.user_id = :uid
                    AND split_part(mm_normalize_name(uc.card_name), ' // ', 1)
                    = split_part(mm_normalize_name(dc.card_name), ' // ', 1)
              )
            ORDER BY price.unit_price DESC NULLS LAST
            LIMIT :limit
        """), {"uid": user["id"], "did": deck_id, "limit": limit}).fetchall()

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
