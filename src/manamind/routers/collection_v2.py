"""Router de la collection — modele par exemplaire.

Complete /api/collection (conserve pour les pages non encore migrees) par une
API qui expose l'edition, la finition, la langue, l'etat et la valeur.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from sqlalchemy import text

from manamind import collection_store as store
from manamind.auth import COOKIE_NAME, get_current_user
from manamind.db.engine import SessionLocal

from ._shared import _json_response

router = APIRouter()


def _user(request: Request) -> dict:
    return get_current_user(mm_token=request.cookies.get(COOKIE_NAME))


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


# ── Collection ────────────────────────────────────────────────────────────────

@router.get("/api/v2/collection")
def api_list(
    request: Request,
    search: str = Query(""),
    colors: str = Query(""),
    types: str = Query(""),
    rarities: str = Query(""),
    sets: str = Query(""),
    finishes: str = Query(""),
    in_deck: str = Query(""),
    sort: str = Query("name"),
    limit: int = Query(60, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Response:
    user = _user(request)
    result = store.list_items(
        user["id"],
        search=search,
        colors=_csv(colors),
        types=_csv(types),
        rarities=_csv(rarities),
        sets=_csv(sets),
        finishes=_csv(finishes),
        in_deck=in_deck,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return _json_response(result)


@router.get("/api/v2/collection/facets")
def api_facets(request: Request) -> Response:
    user = _user(request)
    return _json_response(store.facets(user["id"]))


@router.get("/api/v2/collection/dormant")
def api_dormant(request: Request, limit: int = Query(24, ge=1, le=100)) -> Response:
    user = _user(request)
    return _json_response({"items": store.dormant_items(user["id"], limit)})


@router.post("/api/v2/collection/items")
async def api_add(request: Request) -> Response:
    user = _user(request)
    body = await request.json()

    entries = body.get("entries")
    if isinstance(entries, list) and entries:
        result = store.bulk_add(user["id"], entries)
        return _json_response({"ok": True, **result})

    name = (body.get("card_name") or body.get("name") or "").strip()
    if not name:
        return _json_response({"error": "Nom de carte manquant"}, status_code=400)
    try:
        item = store.add_item(
            user["id"], name,
            quantity=body.get("quantity", 1),
            set_code=body.get("set_code"),
            collector_number=body.get("collector_number"),
            finish=body.get("finish", "nonfoil"),
            language=body.get("language", store.DEFAULT_LANGUAGE),
            condition=body.get("condition"),
            location=body.get("location"),
            note=body.get("note"),
        )
    except ValueError as exc:
        return _json_response({"error": str(exc)}, status_code=400)
    return _json_response({"ok": True, "item": item})


@router.patch("/api/v2/collection/items/{item_id}")
async def api_update(item_id: int, request: Request) -> Response:
    user = _user(request)
    body = await request.json()
    result = store.update_item(user["id"], item_id, **body)
    if result is None:
        return _json_response({"error": "Exemplaire introuvable"}, status_code=404)
    return _json_response({"ok": True, **result})


@router.delete("/api/v2/collection/items/{item_id}")
def api_delete(item_id: int, request: Request) -> Response:
    user = _user(request)
    if not store.delete_item(user["id"], item_id):
        return _json_response({"error": "Exemplaire introuvable"}, status_code=404)
    return _json_response({"ok": True})


@router.delete("/api/v2/collection")
def api_clear(request: Request) -> Response:
    user = _user(request)
    return _json_response({"ok": True, "deleted": store.clear_collection(user["id"])})


# ── Recherche de cartes ──────────────────────────────────────────────────────

_LIGATURES = str.maketrans({"Æ": "Ae", "æ": "ae", "Œ": "Oe", "œ": "oe"})


def _unligature(term: str) -> str:
    """« Æther » -> « Aether » : la base ne connait que la forme depliee."""
    return term.translate(_LIGATURES)


@router.get("/api/v2/cards/suggest")
def api_card_suggest(
    request: Request,
    q: str = Query("", min_length=0),
    limit: int = Query(8, ge=1, le=20),
) -> Response:
    """Autocompletion illustree : nom, impression par defaut et quantite possedee.

    /api/cards/autocomplete ne renvoie que des noms ; l'ajout rapide a besoin
    de la vignette pour que l'utilisateur reconnaisse la carte d'un coup d'oeil.

    La recherche accepte le nom dans n'importe quelle langue imprimee : les
    408 000 noms traduits de scryfall_card_printings sont interroges par leur
    index, en plus du nom anglais. La carte reste identifiee par son nom
    anglais ; le nom trouve est renvoye a part.
    """
    user = _user(request)
    term = q.strip()
    if len(term) < 2:
        return _json_response({"cards": []})

    with SessionLocal() as session:
        rows = session.execute(text("""
            -- Les deux branches passent par un index. ILIKE, et non
            -- LOWER(...) LIKE : la base est en collation French_France.1252,
            -- ou un btree ne sert pas pour LIKE — c'est l'index trigram de
            -- printed_name qui repond, et il exige ILIKE (216 ms -> 30 ms).
            WITH matched AS (
                SELECT id FROM scryfall_cards
                WHERE name ILIKE :prefix OR name ILIKE :prefix_alt
                UNION
                SELECT DISTINCT card_id FROM scryfall_card_printings
                WHERE (printed_name ILIKE :prefix OR printed_name ILIKE :prefix_alt)
                  AND card_id IS NOT NULL
            )
            SELECT c.id, c.name, c.type_line, c.mana_cost, c.mana_value,
                   c.color_identity, c.edhrec_rank, tr.printed_name,
                   p.scryfall_id, p.set_code, p.collector_number, p.rarity,
                   p.image_small, p.image_normal,
                   COALESCE(owned.qty, 0) AS owned
            FROM scryfall_cards c
            JOIN matched m ON m.id = c.id
            -- Nom traduit ayant conduit au resultat, pour le montrer a l'ecran
            LEFT JOIN LATERAL (
                SELECT pr3.printed_name
                FROM scryfall_card_printings pr3
                WHERE pr3.card_id = c.id
                  AND (pr3.printed_name ILIKE :prefix
                       OR pr3.printed_name ILIKE :prefix_alt)
                ORDER BY pr3.printed_name
                LIMIT 1
            ) tr ON TRUE
            JOIN LATERAL (
                SELECT pr.scryfall_id, pr.set_code, pr.collector_number,
                       pr.rarity, pr.image_small, pr.image_normal
                FROM scryfall_card_printings pr
                LEFT JOIN scryfall_mtg_sets ms ON LOWER(ms.code) = LOWER(pr.set_code)
                WHERE pr.card_id = c.id AND pr.lang = 'en'
                -- Une edition standard represente mieux la carte qu'un promo
                ORDER BY (pr.image_normal IS NOT NULL) DESC,
                         (pr.promo IS NOT TRUE) DESC,
                         (COALESCE(ms.set_type, '') NOT IN
                          ('promo', 'memorabilia', 'token', 'minigame')) DESC,
                         pr.released_at DESC NULLS LAST
                LIMIT 1
            ) p ON TRUE
            LEFT JOIN LATERAL (
                SELECT SUM(uc.quantity) AS qty
                FROM user_collection uc
                WHERE uc.user_id = :uid
                  AND mm_normalize_name(uc.card_name) = c.normalized_name
            ) owned ON TRUE
            WHERE c.type_line NOT ILIKE '%Token%'
            -- Correspondance exacte d'abord, puis les noms qui commencent par
            -- le terme, puis ceux qui le contiennent ailleurs.
            ORDER BY (c.name ILIKE :exact) DESC,
                     (LOWER(tr.printed_name) = LOWER(:exact)) DESC,
                     (c.name ILIKE :starts) DESC,
                     c.edhrec_rank ASC NULLS LAST,
                     c.name
            LIMIT :limit
        """), {
            # « contient » et non « commence par » : un nom compose se cherche
            # aussi par son second mot — « isochronique » doit ramener
            # Sceptre isochronique.
            "uid": user["id"], "prefix": f"%{term}%",
            # Scryfall ecrit « Aether », jamais « Æther » : sans cette variante,
            # taper le nom tel qu'il figure sur la carte ne trouvait rien.
            "prefix_alt": f"%{_unligature(term)}%",
            "starts": f"{term}%",
            "exact": term, "limit": limit,
        }).fetchall()

    return _json_response({"cards": [
        {
            "name": r.name,
            "card_name": r.name,
            "printed_name": r.printed_name,
            "scryfall_id": r.scryfall_id,
            "set_code": (r.set_code or "").upper(),
            "collector_number": r.collector_number,
            "rarity": r.rarity,
            "image_small": r.image_small,
            "image_normal": r.image_normal,
            "type_line": r.type_line or "",
            "mana_cost": r.mana_cost or "",
            "mana_value": r.mana_value,
            "color_identity": list(r.color_identity or []),
            "owned": int(r.owned or 0),
        }
        for r in rows
    ]})


@router.post("/api/v2/collection/adjust")
async def api_adjust(request: Request) -> Response:
    """Ajoute ou retire un exemplaire, en designant la carte par son nom.

    Les ecrans d'analyse ne connaissent que des noms : sans cette route, ils
    devraient d'abord retrouver l'identifiant de la ligne de collection.
    """
    user = _user(request)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Corps JSON invalide"}, status_code=400)

    name = (body.get("card_name") or "").strip()
    delta = int(body.get("delta") or 0)
    if not name or delta == 0:
        return _json_response({"error": "card_name et delta sont requis"}, status_code=400)

    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT id, quantity FROM user_collection
            WHERE user_id = :uid AND mm_normalize_name(card_name) = mm_normalize_name(:n)
            ORDER BY quantity DESC, id
        """), {"uid": user["id"], "n": name}).fetchall()

        if not rows:
            if delta < 0:
                return _json_response({"error": "Carte absente de la collection"},
                                      status_code=404)
            store.add_item(user["id"], name, quantity=delta)
            total = delta
        else:
            # Une carte peut occuper plusieurs lignes (editions, finitions) :
            # on ajuste celle qui en a le plus, pour ne pas eparpiller.
            first = rows[0]
            new_qty = first.quantity + delta
            if new_qty <= 0:
                session.execute(text("DELETE FROM user_collection WHERE id = :i"),
                                {"i": first.id})
            else:
                session.execute(text(
                    "UPDATE user_collection SET quantity = :q, updated_at = now() "
                    "WHERE id = :i"), {"q": new_qty, "i": first.id})
            session.commit()
            total = sum(r.quantity for r in rows[1:]) + max(0, new_qty)

    return _json_response({"ok": True, "card_name": name, "quantity": total})


@router.post("/api/v2/cards/resolve")
async def api_cards_resolve(request: Request) -> Response:
    """Resout un lot de noms en illustration, rarete et prix, en une requete.

    Les endpoints d'analyse ne renvoient que des noms de cartes : sans cela,
    chaque page ferait une requete par carte pour afficher les vignettes.
    """
    user = _user(request)
    body = await request.json()
    names = body.get("names") or []
    if not isinstance(names, list) or not names:
        return _json_response({"cards": {}})
    names = [str(n) for n in names[:400]]

    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT n.raw,
                   c.name, c.type_line, c.mana_cost, c.mana_value, c.color_identity,
                   COALESCE(c.game_changer, false) AS game_changer,
                   art.scryfall_id, art.image_small, art.image_normal,
                   art.rarity, art.set_code,
                   price.low_price AS unit_price,
                   COALESCE(owned.qty, 0) AS owned
            FROM unnest(CAST(:names AS text[])) AS n(raw)
            LEFT JOIN LATERAL (
                SELECT sc.id, sc.name, sc.type_line, sc.mana_cost, sc.mana_value,
                       sc.color_identity, sc.game_changer, sc.normalized_name
                FROM scryfall_cards sc
                WHERE sc.normalized_name = mm_normalize_name(n.raw)
                ORDER BY (sc.type_line NOT ILIKE '%Token%') DESC, sc.id
                LIMIT 1
            ) exact ON TRUE

            -- Repli : les listes ne citent souvent que la face avant d'une carte
            -- recto-verso ("Sink into Stupor" pour "Sink into Stupor // ...").
            -- Conditionne a l'echec du match exact pour ne pas scanner inutilement.
            LEFT JOIN LATERAL (
                SELECT sc.id, sc.name, sc.type_line, sc.mana_cost, sc.mana_value,
                       sc.color_identity, sc.game_changer, sc.normalized_name
                FROM scryfall_cards sc
                WHERE exact.id IS NULL
                  AND split_part(sc.normalized_name, ' // ', 1) = mm_normalize_name(n.raw)
                ORDER BY (sc.type_line NOT ILIKE '%Token%') DESC, sc.id
                LIMIT 1
            ) face ON TRUE

            LEFT JOIN LATERAL (
                SELECT COALESCE(exact.id, face.id) AS id,
                       COALESCE(exact.name, face.name) AS name,
                       COALESCE(exact.type_line, face.type_line) AS type_line,
                       COALESCE(exact.mana_cost, face.mana_cost) AS mana_cost,
                       COALESCE(exact.mana_value, face.mana_value) AS mana_value,
                       COALESCE(exact.color_identity, face.color_identity) AS color_identity,
                       COALESCE(exact.game_changer, face.game_changer) AS game_changer,
                       COALESCE(exact.normalized_name, face.normalized_name) AS normalized_name
            ) c ON TRUE
            LEFT JOIN LATERAL (
                SELECT p.scryfall_id, p.image_small, p.image_normal, p.rarity,
                       UPPER(p.set_code) AS set_code
                FROM scryfall_card_printings p
                LEFT JOIN scryfall_mtg_sets ms ON LOWER(ms.code) = LOWER(p.set_code)
                WHERE p.card_id = c.id AND p.lang = 'en'
                ORDER BY (p.image_normal IS NOT NULL) DESC,
                         (p.promo IS NOT TRUE) DESC,
                         (COALESCE(ms.set_type, '') NOT IN ('promo', 'memorabilia')) DESC,
                         p.released_at DESC NULLS LAST
                LIMIT 1
            ) art ON TRUE
            -- Prix de reference : low_price de l'edition la moins chere
            LEFT JOIN card_min_price price ON price.card_id = c.id
            LEFT JOIN LATERAL (
                SELECT SUM(uc.quantity) AS qty
                FROM user_collection uc
                WHERE uc.user_id = :uid
                  AND mm_normalize_name(uc.card_name) = c.normalized_name
            ) owned ON TRUE
        """), {"names": names, "uid": user["id"]}).fetchall()

    cards = {}
    for r in rows:
        cards[r.raw] = {
            "card_name": r.name or r.raw,
            "scryfall_id": r.scryfall_id,
            "image_small": r.image_small,
            "image_normal": r.image_normal,
            "rarity": r.rarity,
            "set_code": r.set_code,
            "type_line": r.type_line or "",
            "mana_cost": r.mana_cost or "",
            "mana_value": r.mana_value,
            "color_identity": list(r.color_identity or []),
            "game_changer": bool(r.game_changer),
            "unit_price": float(r.unit_price) if r.unit_price is not None else None,
            "owned": int(r.owned or 0),
        }
    return _json_response({"cards": cards})


@router.get("/api/v2/cards/{card_name}/printings")
def api_card_printings(
    card_name: str,
    request: Request,
    limit: int = Query(60, ge=1, le=200),
) -> Response:
    """Impressions disponibles d'une carte, pour choisir l'edition exacte."""
    _user(request)
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT p.scryfall_id, p.set_code, p.collector_number, p.rarity,
                   p.image_small, p.image_normal, p.released_at,
                   s.name AS set_name, s.icon_svg_uri
            FROM scryfall_card_printings p
            JOIN scryfall_cards c ON c.id = p.card_id
            LEFT JOIN scryfall_mtg_sets s ON LOWER(s.code) = LOWER(p.set_code)
            WHERE c.normalized_name = mm_normalize_name(:name) AND p.lang = 'en'
            ORDER BY p.released_at DESC NULLS LAST
            LIMIT :limit
        """), {"name": card_name, "limit": limit}).fetchall()

    return _json_response({"printings": [
        {
            "scryfall_id": r.scryfall_id,
            "set_code": (r.set_code or "").upper(),
            "set_name": r.set_name,
            "set_icon": r.icon_svg_uri,
            "collector_number": r.collector_number,
            "rarity": r.rarity,
            "image_small": r.image_small,
            "image_normal": r.image_normal,
            "released_at": r.released_at.isoformat() if r.released_at else None,
        }
        for r in rows
    ]})


# ── Extensions (ecran ouverture de boosters) ─────────────────────────────────

@router.get("/api/v2/sets")
def api_sets(
    request: Request,
    search: str = Query(""),
    limit: int = Query(60, ge=1, le=1000),
) -> Response:
    """Extensions ouvrables, les plus recentes d'abord.

    Liste noire plutot que blanche : l'ecran de selection doit montrer toutes
    les extensions, y compris les coffrets et decks preconstruits. Seul ce qui
    ne s'ouvre pas est ecarte (jetons, promos, memorabilia, minijeux).
    """
    _user(request)
    params: dict = {"limit": limit}
    where = [
        "s.set_type NOT IN ('token', 'promo', 'memorabilia', 'minigame')",
        "s.card_count > 0",
        "s.released_at IS NOT NULL",
    ]
    if search:
        where.append("(s.name ILIKE :q OR s.code ILIKE :q)")
        params["q"] = f"%{search}%"

    with SessionLocal() as session:
        rows = session.execute(text(f"""
            SELECT s.code, s.name, s.set_type, s.released_at,
                   s.card_count, s.icon_svg_uri
            FROM scryfall_mtg_sets s
            WHERE {' AND '.join(where)}
            ORDER BY s.released_at DESC
            LIMIT :limit
        """), params).fetchall()

    return _json_response({"sets": [
        {
            "code": r.code.upper(),
            "name": r.name,
            "set_type": r.set_type,
            "released_at": r.released_at.isoformat() if r.released_at else None,
            "card_count": r.card_count,
            "icon": r.icon_svg_uri,
        }
        for r in rows
    ]})


@router.get("/api/v2/sets/{code}/cards")
def api_set_cards(
    code: str,
    request: Request,
    search: str = Query(""),
    rarity: str = Query(""),
    limit: int = Query(400, ge=1, le=600),
) -> Response:
    """Cartes d'une extension, avec le nombre deja possede par l'utilisateur."""
    user = _user(request)
    params: dict = {"code": code.lower(), "uid": user["id"], "limit": limit}
    where = ["LOWER(p.set_code) = :code", "p.lang = 'en'"]
    if search:
        where.append("c.name ILIKE :q")
        params["q"] = f"%{search}%"
    if rarity:
        where.append("p.rarity = ANY(:rarities)")
        params["rarities"] = _csv(rarity.lower())

    with SessionLocal() as session:
        rows = session.execute(text(f"""
            SELECT DISTINCT ON (p.collector_number)
                   p.scryfall_id, p.collector_number, p.rarity,
                   p.image_small, p.image_normal, p.set_code,
                   c.name, c.type_line, c.mana_cost, c.mana_value,
                   c.color_identity,
                   COALESCE(owned.qty, 0) AS owned
            FROM scryfall_card_printings p
            JOIN scryfall_cards c ON c.id = p.card_id
            LEFT JOIN LATERAL (
                SELECT SUM(uc.quantity) AS qty
                FROM user_collection uc
                WHERE uc.user_id = :uid
                  AND mm_normalize_name(uc.card_name) = c.normalized_name
            ) owned ON TRUE
            WHERE {' AND '.join(where)}
            ORDER BY p.collector_number,
                     (p.image_normal IS NOT NULL) DESC
            LIMIT :limit
        """), params).fetchall()

    def _sort_key(row):
        num = row.collector_number or ""
        digits = "".join(ch for ch in num if ch.isdigit())
        return (int(digits) if digits else 99999, num)

    cards = sorted(rows, key=_sort_key)
    return _json_response({"cards": [
        {
            "scryfall_id": r.scryfall_id,
            "card_name": r.name,
            "collector_number": r.collector_number,
            "rarity": r.rarity,
            "set_code": (r.set_code or "").upper(),
            "image_small": r.image_small,
            "image_normal": r.image_normal,
            "type_line": r.type_line or "",
            "mana_cost": r.mana_cost or "",
            "mana_value": r.mana_value,
            "color_identity": list(r.color_identity or []),
            "owned": int(r.owned or 0),
        }
        for r in cards
    ]})
