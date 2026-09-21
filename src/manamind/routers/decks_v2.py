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
from manamind.commander_curve import reference_for
from manamind.commanders import MAX_COMMANDERS, join_commanders, split_commanders
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
        -- L'edition que l'utilisateur a choisie pour cette carte passe avant
        -- tout : le reste n'est qu'un defaut, applique faute de choix.
        -- Les visuels Secret Lair et Marvel Universe passent en dernier : ils
        -- ne representent pas la carte, mais quelques-unes n'existent que la.
        ORDER BY (p.scryfall_id = (
                     SELECT pref.scryfall_id
                     FROM user_preferred_printings pref
                     WHERE pref.user_id = :uid
                       AND pref.card_key = split_part(
                             mm_normalize_name({name_expr}), ' // ', 1)
                 )) DESC NULLS LAST,
                 (p.set_code NOT ILIKE 'sl%%' AND LOWER(p.set_code) NOT IN ('mar', 'lmar')) DESC,
                 (p.image_normal IS NOT NULL) DESC,
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

    # Le dernier deck choisi, pour que les ecrans d'analyse s'ouvrent dessus.
    # Il est relu ici plutot que par une requete a part : le selecteur charge
    # deja cette liste, et il lui faut les deux ensemble.
    with SessionLocal() as session:
        last = session.execute(text("""
            SELECT last_deck_id FROM user_profiles WHERE user_id = :uid
        """), {"uid": user["id"]}).scalar()

    known = {d["deck_id"] for d in decks}
    return _json_response({
        "decks": decks,
        # Un deck supprime depuis ne vaut plus d'etre propose.
        "last_deck_id": last if last in known else None,
    })


@router.post("/api/v2/decks/last")
async def api_set_last_deck(request: Request) -> Response:
    """Retient le deck sur lequel l'utilisateur travaille.

    Les ecrans d'analyse s'ouvrent dessus a la visite suivante, quel que soit
    celui ou le choix a ete fait — et quel que soit l'appareil, la preference
    vivant avec le compte.
    """
    user = _user(request)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Corps JSON invalide"}, status_code=400)

    deck_id = (body.get("deck_id") or "").strip()
    if not deck_id:
        return _json_response({"error": "deck_id requis"}, status_code=400)

    with SessionLocal() as session:
        owned = session.execute(text("""
            SELECT 1 FROM user_moxfield_decks
            WHERE user_id = :uid AND deck_id = :did
        """), {"uid": user["id"], "did": deck_id}).scalar()
        if not owned:
            return _json_response({"error": "Deck introuvable"}, status_code=404)

        session.execute(text("""
            INSERT INTO user_profiles (user_id, last_deck_id)
            VALUES (:uid, :did)
            ON CONFLICT (user_id) DO UPDATE
              SET last_deck_id = EXCLUDED.last_deck_id, updated_at = NOW()
        """), {"uid": user["id"], "did": deck_id})
        session.commit()

    return _json_response({"ok": True, "last_deck_id": deck_id})


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
                  -- Les cartes d'un deck supprime restent en table : comptees,
                  -- elles retenaient des exemplaires pour un deck qui n'est
                  -- plus, et la carte passait pour entierement engagee.
                  AND EXISTS (
                      SELECT 1 FROM user_moxfield_decks d
                      WHERE d.user_id = dc.user_id AND d.deck_id = dc.deck_id
                  )
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
    """Compose les commandants du deck a partir d'une de ses cartes.

    Trois gestes, selon `mode` : « replace » installe la carte comme unique
    commandant (le cas d'un import qui n'a pas su l'identifier, enregistre sous
    « Unknown »), « add » lui adjoint un second — Partner, Background, Doctor's
    companion — et « remove » en retire un.

    Les cartes portent le nom du commandant pour les analyses qui raisonnent
    par commandant : les deux tables doivent changer ensemble, d'ou la
    transaction.
    """
    user = _user(request)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Corps JSON invalide"}, status_code=400)

    card_name = (body.get("card_name") or "").strip()
    if not card_name:
        return _json_response({"error": "Nom de carte manquant"}, status_code=400)

    mode = (body.get("mode") or "replace").strip().lower()
    if mode not in {"replace", "add", "remove"}:
        return _json_response({"error": "Mode inconnu"}, status_code=400)

    with SessionLocal() as session:
        current = session.execute(text("""
            SELECT commander FROM user_moxfield_decks
            WHERE user_id = :uid AND deck_id = :did
        """), {"uid": user["id"], "did": deck_id}).scalar()
        if current is None:
            return _json_response({"error": "Deck introuvable"}, status_code=404)

        existing = split_commanders(current)

        if mode == "remove":
            remaining = [n for n in existing if n.lower() != card_name.lower()]
            if len(remaining) == len(existing):
                return _json_response(
                    {"error": "Cette carte n'est pas un commandant de ce deck"},
                    status_code=400)
            if not remaining:
                return _json_response(
                    {"error": "Un deck garde au moins un commandant"}, status_code=400)
            commander = join_commanders(remaining)
        else:
            in_deck = session.execute(text("""
                SELECT 1 FROM user_deck_cards
                WHERE user_id = :uid AND deck_id = :did
                  AND mm_normalize_name(card_name) = mm_normalize_name(:card)
            """), {"uid": user["id"], "did": deck_id, "card": card_name}).scalar()
            if not in_deck:
                return _json_response(
                    {"error": "Cette carte ne fait pas partie du deck"}, status_code=400)

            wanted = [card_name] if mode == "replace" else [*existing, card_name]
            commander = join_commanders(wanted)
            if len(split_commanders(commander)) > MAX_COMMANDERS:
                return _json_response(
                    {"error": f"Un deck ne peut pas avoir plus de {MAX_COMMANDERS} "
                              "commandants"}, status_code=400)

        # Les SELECT ci-dessus ont deja ouvert la transaction : les deux UPDATE
        # y prennent place et sont valides ensemble par le commit final.
        # Le commandant reste ecrit sur les cartes pour les analyses qui
        # raisonnent par commandant, mais il ne les rattache plus au deck.
        session.execute(text("""
            UPDATE user_deck_cards SET commander = :new
            WHERE user_id = :uid AND deck_id = :did
        """), {"uid": user["id"], "did": deck_id, "new": commander})
        session.execute(text("""
            UPDATE user_moxfield_decks SET commander = :new, locally_modified = TRUE
            WHERE user_id = :uid AND deck_id = :did
        """), {"uid": user["id"], "did": deck_id, "new": commander})
        session.commit()

    return _json_response({
        "ok": True,
        "commander": commander,
        "commanders": split_commanders(commander),
    })


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


# Jetons qu'une liste met en jeu. La reponse vient du catalogue, pas du texte
# d'oracle : « create a 1/1 white Soldier creature token » ne dit pas lequel des
# trois jetons Soldier 1/1 blancs de Scryfall est le bon, alors que le champ
# all_parts le nomme. scryfall_card_parts porte cette liaison.
#
# La cle d'un jeton est l'oracle_id du jeton, et non l'impression citee : un
# meme Tresor est reference par autant d'impressions qu'il a d'editions, et il
# ne doit paraitre qu'une fois. Repli sur le nom en minuscules quand
# l'impression citee manque du catalogue.
_DECK_TOKENS_SQL = """
    WITH deck AS (
        SELECT DISTINCT dc.card_name, mm_normalize_name(dc.card_name) AS nname
        FROM user_deck_cards dc
        WHERE dc.user_id = :uid AND dc.deck_id = :did
    ),
    -- Un LATERAL par carte, et non une jointure large : un nom normalise peut
    -- designer a la fois une carte et un jeton du meme nom (Clue, Treasure),
    -- et c'est la carte qu'il faut retenir. Le meme depart que le detail du deck.
    cartes AS (
        SELECT d.card_name, sc.id AS card_id
        FROM deck d
        JOIN LATERAL (
            SELECT c.id FROM scryfall_cards c
            WHERE c.normalized_name = d.nname
            ORDER BY (c.type_line NOT ILIKE '%%Token%%') DESC, c.id
            LIMIT 1
        ) sc ON TRUE
    ),
    liaisons AS (
        SELECT ca.card_name, p.part_scryfall_id, p.part_name, p.part_type_line
        FROM cartes ca
        JOIN scryfall_card_parts p
          ON p.card_id = ca.card_id AND p.component = 'token'
    )
    SELECT COALESCE(tc.oracle_id, LOWER(l.part_name)) AS token_key,
           COALESCE(tc.name, l.part_name) AS name,
           COALESCE(tc.type_line, l.part_type_line) AS type_line,
           tc.power, tc.toughness, tc.colors,
           array_agg(DISTINCT l.card_name) AS sources
    FROM liaisons l
    LEFT JOIN scryfall_card_printings pp ON pp.scryfall_id = l.part_scryfall_id
    LEFT JOIN scryfall_cards tc ON tc.id = pp.card_id
    GROUP BY 1, 2, 3, tc.power, tc.toughness, tc.colors
    ORDER BY 2
"""


def _deck_existe(session, user_id: int, deck_id: str) -> bool:
    return session.execute(text("""
        SELECT 1 FROM user_moxfield_decks WHERE user_id = :uid AND deck_id = :did
    """), {"uid": user_id, "did": deck_id}).first() is not None


@router.get("/api/v2/decks/{deck_id}/tokens")
def api_deck_tokens(deck_id: str, request: Request) -> Response:
    """Jetons que le deck met en jeu, et ceux deja mis de cote."""
    user = _user(request)

    with SessionLocal() as session:
        if not _deck_existe(session, user["id"], deck_id):
            return _json_response({"error": "Deck introuvable"}, status_code=404)

        requis = session.execute(
            text(_DECK_TOKENS_SQL), {"uid": user["id"], "did": deck_id}).fetchall()
        possedes = session.execute(text("""
            SELECT token_key, token_name, token_type_line, quantity
            FROM user_deck_tokens
            WHERE user_id = :uid AND deck_id = :did
        """), {"uid": user["id"], "did": deck_id}).fetchall()

    ajoutes = {row.token_key: row for row in possedes}
    tokens = []
    for row in requis:
        possede = ajoutes.pop(row.token_key, None)
        tokens.append({
            "key": row.token_key,
            "name": row.name,
            "type_line": row.type_line or "",
            "power": row.power,
            "toughness": row.toughness,
            "colors": list(row.colors or []),
            "sources": list(row.sources or []),
            "added": possede is not None,
            "quantity": int(possede.quantity) if possede else 0,
        })

    # Ce qui reste dans `ajoutes` a ete mis de cote puis n'est plus reclame par
    # aucune carte — un jeton ajoute a la main, ou la carte qui l'appelait a
    # quitte le deck. Le taire ferait disparaitre une ligne que l'utilisateur a
    # creee, sans qu'il puisse la retirer.
    extras = [
        {
            "key": row.token_key,
            "name": row.token_name,
            "type_line": row.token_type_line or "",
            "power": None,
            "toughness": None,
            "colors": [],
            "sources": [],
            "added": True,
            "quantity": int(row.quantity),
        }
        for row in sorted(ajoutes.values(), key=lambda r: r.token_name.lower())
    ]

    return _json_response({
        "tokens": tokens,
        "extras": extras,
        "added_count": sum(1 for t in tokens if t["added"]) + len(extras),
        "missing_count": sum(1 for t in tokens if not t["added"]),
    })


@router.post("/api/v2/decks/{deck_id}/tokens")
async def api_add_deck_token(deck_id: str, request: Request) -> Response:
    """Met un jeton de cote pour ce deck."""
    user = _user(request)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Corps JSON invalide"}, status_code=400)

    key = (body.get("key") or "").strip()
    name = (body.get("name") or "").strip()
    type_line = (body.get("type_line") or "").strip() or None
    if not (key and name):
        return _json_response({"error": "key et name sont requis"}, status_code=400)

    try:
        quantity = int(body.get("quantity") or 1)
    except (TypeError, ValueError):
        return _json_response({"error": "quantity doit être un entier"}, status_code=400)
    # Bornee : la colonne est un smallint, et aucune partie ne demande mille
    # exemplaires du meme jeton.
    quantity = max(1, min(quantity, 99))

    with SessionLocal() as session:
        if not _deck_existe(session, user["id"], deck_id):
            return _json_response({"error": "Deck introuvable"}, status_code=404)
        session.execute(text("""
            INSERT INTO user_deck_tokens
                (user_id, deck_id, token_key, token_name, token_type_line, quantity)
            VALUES (:uid, :did, :key, :name, :type_line, :qty)
            ON CONFLICT (user_id, deck_id, token_key) DO UPDATE
               SET quantity = :qty,
                   token_name = EXCLUDED.token_name,
                   token_type_line = EXCLUDED.token_type_line
        """), {"uid": user["id"], "did": deck_id, "key": key, "name": name,
               "type_line": type_line, "qty": quantity})
        session.commit()

    return _json_response({"ok": True, "quantity": quantity})


@router.delete("/api/v2/decks/{deck_id}/tokens/{token_key:path}")
def api_drop_deck_token(deck_id: str, token_key: str, request: Request) -> Response:
    """Retire un jeton mis de cote."""
    user = _user(request)
    with SessionLocal() as session:
        result = session.execute(text("""
            DELETE FROM user_deck_tokens
            WHERE user_id = :uid AND deck_id = :did AND token_key = :key
        """), {"uid": user["id"], "did": deck_id, "key": token_key})
        session.commit()
    if not result.rowcount:
        return _json_response({"error": "Jeton introuvable"}, status_code=404)
    return _json_response({"ok": True})


@router.get("/api/v2/stats/mana-curve")
def api_commander_mana_curve(
    request: Request,
    commander: str = Query(..., min_length=1, max_length=200),
) -> Response:
    """Courbe et cout moyen des decks publics jouant ce commandant.

    Sert de point de comparaison aux ecrans qui montrent la courbe d'un deck.
    Un commandant absent de la base publique n'est pas une erreur : la page
    s'en passe et n'affiche que le cout du deck.
    """
    _user(request)

    # La forme canonique — les deux noms d'une paire, ranges dans l'ordre — est
    # celle sous laquelle les decks publics sont indexes.
    name = join_commanders(split_commanders(commander))
    reference = reference_for(name)
    if reference is None:
        return _json_response({"reference": None})

    return _json_response({"reference": reference})
