"""Router collection — routes /api/collection/*, /api/collection-commanders, /api/card-*."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from manamind.routers._shared import _json_response

router = APIRouter()


@router.get("/api/cards/search")
def search_cards(
    q: str = Query(default="", description="Texte à rechercher dans le nom des cartes"),
    limit: int = Query(default=100, ge=1, le=100),
) -> JSONResponse:
    """
    Recherche de cartes par nom (contains, case-insensitive).
    Retourne au maximum 100 résultats triés par nom.
    Requiert que la base PostgreSQL soit configurée (.env) et que l'import ait été lancé.
    """
    from sqlalchemy import func, select
    from sqlalchemy.orm import aliased
    try:
        from src.manamind.db.engine import SessionLocal
        from src.manamind.db.models.card import Card
        from src.manamind.db.models.card_price import CardPrice
        from src.manamind.db.models.card_printing import CardPrinting
        _DB_AVAILABLE = SessionLocal is not None
    except Exception:
        SessionLocal = None
        Card = None
        CardPrinting = None
        CardPrice = None
        _DB_AVAILABLE = False

    if not _DB_AVAILABLE:
        return JSONResponse(
            {
                "error": (
                    "Base de données non configurée. "
                    "Crée un fichier .env avec DATABASE_URL puis lance "
                    "python scripts/import_scryfall_cards.py"
                )
            },
            status_code=503,
        )

    q = q.strip()
    if len(q) < 2:
        return JSONResponse({"cards": [], "total": 0, "query": q})

    try:
        with SessionLocal() as session:
            # Sous-requête 1a : rang de chaque impression par prix EUR décroissant
            # row_number() = 1 → impression la plus chère de la carte
            expensive_rank_subq = (
                select(
                    CardPrinting.card_id,
                    CardPrinting.id.label("pid"),
                    CardPrinting.image_normal,
                    CardPrinting.scryfall_uri,
                    func.row_number().over(
                        partition_by=CardPrinting.card_id,
                        order_by=CardPrice.price.desc().nulls_last(),
                    ).label("rn"),
                )
                .join(CardPrice, CardPrinting.id == CardPrice.printing_id)
                .where(
                    CardPrice.currency == "eur",
                    CardPrice.price_type == "regular",
                    CardPrice.price > 0,
                )
                .subquery()
            )
            expensive_printing_subq = (
                select(
                    expensive_rank_subq.c.card_id,
                    expensive_rank_subq.c.image_normal,
                    expensive_rank_subq.c.scryfall_uri,
                )
                .where(expensive_rank_subq.c.rn == 1)
                .subquery()
            )

            # Sous-requête 1b : première impression (fallback si aucun prix disponible)
            first_printing_subq = (
                select(
                    CardPrinting.card_id,
                    func.min(CardPrinting.id).label("pid"),
                )
                .group_by(CardPrinting.card_id)
                .subquery()
            )
            FallbackPrinting = aliased(CardPrinting)

            # Sous-requête 2 : prix EUR minimum (le moins cher) parmi toutes les impressions
            price_subq = (
                select(
                    CardPrinting.card_id,
                    func.min(CardPrice.price).label("eur_price"),
                )
                .join(CardPrice, CardPrinting.id == CardPrice.printing_id)
                .where(
                    CardPrice.currency == "eur",
                    CardPrice.price_type == "regular",
                    CardPrice.price > 0,
                )
                .group_by(CardPrinting.card_id)
                .subquery()
            )

            stmt = (
                select(
                    Card,
                    func.coalesce(
                        expensive_printing_subq.c.image_normal,
                        FallbackPrinting.image_normal,
                    ).label("image_normal"),
                    func.coalesce(
                        expensive_printing_subq.c.scryfall_uri,
                        FallbackPrinting.scryfall_uri,
                    ).label("scryfall_uri"),
                    price_subq.c.eur_price,
                )
                .outerjoin(expensive_printing_subq, Card.id == expensive_printing_subq.c.card_id)
                .outerjoin(first_printing_subq, Card.id == first_printing_subq.c.card_id)
                .outerjoin(FallbackPrinting, FallbackPrinting.id == first_printing_subq.c.pid)
                .outerjoin(price_subq, Card.id == price_subq.c.card_id)
                # ilike = ILIKE PostgreSQL : case-insensitive, paramétré → pas d'injection SQL
                .where(Card.name.ilike(f"%{q}%"))
                # Tri par popularité méta (1 = plus populaire), sans rank en dernier
                .order_by(Card.edhrec_rank.asc().nulls_last(), Card.name)
                .limit(limit)
            )

            rows = session.execute(stmt).all()

            cards = []
            for card, image_normal, scryfall_uri, eur_price in rows:
                oracle_text = card.oracle_text or ""
                if len(oracle_text) > 280:
                    oracle_text = oracle_text[:280] + "…"

                cards.append({
                    "name": card.name,
                    "mana_cost": card.mana_cost,
                    "type_line": card.type_line,
                    "oracle_text": oracle_text,
                    "legal_commander": card.legal_commander,
                    "colors": card.colors or [],
                    "edhrec_rank": card.edhrec_rank,
                    "eur_price": float(eur_price) if eur_price is not None else None,
                    "image_normal": image_normal,
                    "scryfall_uri": scryfall_uri,
                })

            return JSONResponse({"cards": cards, "total": len(cards), "query": q})

    except Exception as exc:
        return JSONResponse(
            {"error": f"Erreur base de données : {exc}"},
            status_code=500,
        )


@router.get("/api/cards/image")
async def card_image(
    name: str = Query(..., description="Nom exact ou approché de la carte"),
) -> JSONResponse:
    """
    Retourne l'URL de l'image normale d'une carte.
    Cherche d'abord dans la DB locale, puis appelle Scryfall côté serveur (pas de CORS).
    """
    import httpx
    try:
        from src.manamind.db.engine import SessionLocal
        from src.manamind.db.models.card import Card
        from src.manamind.db.models.card_printing import CardPrinting
        from sqlalchemy import select
        _DB_AVAILABLE = SessionLocal is not None
    except Exception:
        SessionLocal = None
        Card = None
        CardPrinting = None
        _DB_AVAILABLE = False

    name = name.strip()

    # Scryfall (côté serveur → pas de CORS) — source unique pour image + colors + booster
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.get(
                "https://api.scryfall.com/cards/named",
                params={"fuzzy": name},
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                url = (
                    data.get("image_uris", {}).get("normal")
                    or (data.get("card_faces") or [{}])[0].get("image_uris", {}).get("normal")
                )
                colors = data.get("color_identity") or data.get("colors") or []
                rarity = data.get("rarity", "")
                set_code = data.get("set", "").upper()
                if url:
                    return _json_response({"url": url, "colors": colors, "rarity": rarity, "set": set_code})
    except Exception:
        pass

    # Fallback DB locale (image uniquement, pas de metadata couleur)
    if _DB_AVAILABLE:
        try:
            from sqlalchemy import select
            with SessionLocal() as session:
                stmt = (
                    select(CardPrinting.image_normal)
                    .join(Card, Card.id == CardPrinting.card_id)
                    .where(
                        Card.name.ilike(name),
                        CardPrinting.image_normal.isnot(None),
                        CardPrinting.lang == "en",
                    )
                    .limit(1)
                )
                row = session.execute(stmt).first()
                if row and row[0]:
                    return _json_response({"url": row[0], "colors": [], "booster": None})
        except Exception:
            pass

    return _json_response({"url": None}, status_code=404)


@router.get("/api/card-source")
def api_card_source(request: Request, name: str = Query(...)) -> JSONResponse:
    """
    Retourne la source d'une carte parmi : 'in_deck', 'collection', 'opened_sets', ou None.
    Même logique de priorité que deck_build.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    import unicodedata as _ud
    def _norm(s: str) -> str:
        s = _ud.normalize("NFKD", s)
        return "".join(c for c in s if not _ud.combining(c)).lower().strip()

    card_norm = _norm(name)

    # 1. Dans un deck de l'utilisateur ? — un seul JOIN au lieu de N requêtes
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    with SessionLocal() as sess:
        deck_rows = sess.execute(_t("""
            SELECT DISTINCT d.commander
            FROM user_moxfield_decks d
            JOIN user_deck_cards dc
              ON dc.user_id = d.user_id
             AND LOWER(TRIM(dc.commander)) = LOWER(TRIM(d.commander))
            WHERE d.user_id = :uid
              AND LOWER(TRIM(dc.card_name)) = LOWER(TRIM(:card))
        """), {"uid": user["id"], "card": name}).fetchall()
    decks_found: list[str] = [r.commander for r in deck_rows if r.commander]
    if decks_found:
        return _json_response({"source": "in_deck", "decks": decks_found})

    # 2. Dans la collection de l'utilisateur ?
    with SessionLocal() as sess:
        row = sess.execute(_t("""
            SELECT 1 FROM user_collection
            WHERE user_id = :uid AND LOWER(TRIM(card_name)) = LOWER(TRIM(:name))
            LIMIT 1
        """), {"uid": user["id"], "name": name}).fetchone()
    if row:
        return _json_response({"source": "collection", "decks": []})

    # 3. C/U dans un set ouvert (user_opened_sets DB) ?
    from manamind.collection_advisor import load_opened_set_cards
    opened_cards = load_opened_set_cards(user_id=user["id"])
    if card_norm in opened_cards:
        return _json_response({"source": "opened_sets", "decks": []})

    return _json_response({"source": None, "decks": []})


@router.get("/api/card-in-decks")
def api_card_in_decks(request: Request, name: str = Query(...)) -> JSONResponse:
    """
    Retourne la liste des commandants dont le deck contient la carte `name`.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    # Un seul JOIN au lieu de N requêtes séparées par deck
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    with SessionLocal() as sess:
        deck_rows = sess.execute(_t("""
            SELECT DISTINCT d.commander
            FROM user_moxfield_decks d
            JOIN user_deck_cards dc
              ON dc.user_id = d.user_id
             AND LOWER(TRIM(dc.commander)) = LOWER(TRIM(d.commander))
            WHERE d.user_id = :uid
              AND LOWER(TRIM(dc.card_name)) = LOWER(TRIM(:card))
        """), {"uid": user["id"], "card": name}).fetchall()
    decks_found: list[str] = [r.commander for r in deck_rows if r.commander]
    return _json_response({"decks": decks_found})


@router.get("/api/cards/price")
def get_card_price(
    name: str = Query(..., description="Nom exact de la carte (anglais)"),
) -> JSONResponse:
    """
    Retourne le prix EUR minimum (regular) de la carte parmi toutes ses impressions.
    Cherche d'abord en DB, puis fallback Scryfall.
    """
    try:
        from src.manamind.db.engine import SessionLocal
        from src.manamind.db.models.card import Card
        from src.manamind.db.models.card_price import CardPrice
        from src.manamind.db.models.card_printing import CardPrinting
        from sqlalchemy import func, select
        _DB_AVAILABLE = SessionLocal is not None
    except Exception:
        SessionLocal = None
        Card = None
        CardPrinting = None
        CardPrice = None
        _DB_AVAILABLE = False

    if _DB_AVAILABLE:
        try:
            with SessionLocal() as session:
                stmt = (
                    select(func.min(CardPrice.price))
                    .join(CardPrinting, CardPrinting.id == CardPrice.printing_id)
                    .join(Card, Card.id == CardPrinting.card_id)
                    .where(
                        Card.name.ilike(name),
                        CardPrice.currency == "eur",
                        CardPrice.price_type == "regular",
                        CardPrice.price > 0,
                    )
                )
                row = session.execute(stmt).first()
                if row and row[0] is not None:
                    return _json_response({"price": float(row[0]), "currency": "EUR"})
        except Exception:
            pass

    return _json_response({"price": None, "currency": "EUR"})


@router.get("/api/cards/autocomplete")
def autocomplete_cards(
    q: str = Query(default="", description="Préfixe à rechercher"),
    limit: int = Query(default=8, ge=1, le=20),
) -> JSONResponse:
    """
    Autocomplete sur les noms de cartes (starts-with, case-insensitive).
    Cherche dans Card.name (anglais) ET CardPrinting.printed_name (toutes langues).
    Retourne les noms anglais canoniques dédupliqués.
    """
    try:
        from src.manamind.db.engine import SessionLocal
        from src.manamind.db.models.card import Card
        from src.manamind.db.models.card_printing import CardPrinting
        from sqlalchemy import select
        _DB_AVAILABLE = SessionLocal is not None
    except Exception:
        SessionLocal = None
        Card = None
        CardPrinting = None
        _DB_AVAILABLE = False

    if not _DB_AVAILABLE:
        return _json_response({"names": []})

    q = q.strip()
    if len(q) < 2:
        return _json_response({"names": []})

    try:
        with SessionLocal() as session:
            # Noms anglais commençant par q
            en_stmt = (
                select(Card.name)
                .where(Card.name.ilike(f"{q}%"))
                .order_by(Card.edhrec_rank.asc().nulls_last(), Card.name)
                .limit(limit)
            )
            en_names = [row[0] for row in session.execute(en_stmt).all()]

            # Noms traduits commençant par q → récupérer le nom anglais canonique
            tr_stmt = (
                select(Card.name)
                .join(CardPrinting, Card.id == CardPrinting.card_id)
                .where(
                    CardPrinting.printed_name.isnot(None),
                    CardPrinting.printed_name.ilike(f"{q}%"),
                )
                .order_by(Card.edhrec_rank.asc().nulls_last(), Card.name)
                .limit(limit)
            )
            tr_names = [row[0] for row in session.execute(tr_stmt).all()]

            # Fusionner en conservant l'ordre et dédupliquer
            seen: set[str] = set()
            names: list[str] = []
            for name in en_names + tr_names:
                if name not in seen:
                    seen.add(name)
                    names.append(name)
                if len(names) >= limit:
                    break

            return _json_response({"names": names})

    except Exception as exc:
        return _json_response({"names": [], "error": str(exc)})


@router.get("/api/collection-suggest")
def api_collection_suggest(
    request: Request,
    top: int = Query(default=40, ge=1, le=100),
    commander: str | None = Query(default=None),
) -> JSONResponse:
    """
    Analyse la collection et les decks de l'utilisateur,
    et retourne les cartes disponibles ayant le meilleur taux d'inclusion.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.collection_advisor import suggest_from_collection_for_user
    result = suggest_from_collection_for_user(user["id"], top_n=top, commander_filter=commander or None)
    return _json_response(result)


@router.get("/api/deck-composition")
def api_deck_composition(
    request: Request,
    commander: str = Query(...),
) -> JSONResponse:
    """
    Retourne la composition par type du deck personnel et la moyenne méta
    pour le commandant donné.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.collection_advisor import compute_deck_composition_for_user
    result = compute_deck_composition_for_user(user["id"], commander)
    return _json_response(result)


@router.get("/api/deck-moves")
def api_deck_moves(
    request: Request,
    top: int = Query(default=60, ge=1, le=100),
) -> JSONResponse:
    """
    Retourne les cartes présentes dans un deck mais qui auraient un meilleur taux
    d'inclusion dans un autre deck du même joueur, classées par gain décroissant.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.collection_advisor import suggest_moves_for_user
    result = suggest_moves_for_user(user["id"], top_n=top)
    return _json_response(result)


@router.get("/api/deck-trim")
def api_deck_trim(
    request: Request,
    commander: str = Query(..., description="Nom exact du commandant"),
) -> JSONResponse:
    """
    Retourne les cartes du deck du commandant triées par inclusion_rate croissant.
    Les candidates à la coupe sont en tête de liste.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.collection_advisor import suggest_cuts_for_user
    result = suggest_cuts_for_user(user["id"], commander_name=commander)
    return _json_response(result)


@router.get("/api/deck-baseland-counts")
def api_deck_baseland_counts(
    request: Request,
    commander: str = Query(...),
) -> JSONResponse:
    """Retourne { counts: { 'Plains': 5, 'Island': 3, ... } } pour les terrains de base du deck."""
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.user_decks import get_deck_cards
    from manamind.collection_advisor import _normalize
    BASIC_LANDS = {"plains": "Plains", "island": "Island", "swamp": "Swamp",
                   "mountain": "Mountain", "forest": "Forest", "wastes": "Wastes"}
    entries = get_deck_cards(user["id"], commander)
    counts: dict[str, int] = {}
    for name, qty in entries:
        norm = _normalize(name)
        if norm in BASIC_LANDS:
            canonical = BASIC_LANDS[norm]
            counts[canonical] = counts.get(canonical, 0) + qty
    return _json_response({"counts": counts})


@router.get("/api/my-decks")
def api_my_decks(request: Request) -> JSONResponse:
    """
    Retourne la liste des decks de l'utilisateur connecté.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.user_decks import load_config_for_user

    raw = load_config_for_user(user["id"])
    decks = []
    for entry in raw:
        cmd = entry.get("commander", "")
        if not cmd:
            continue
        d = {
            "commander": cmd,
            "source": "moxfield",
            "deck_id": entry["deck_id"],
            "url": entry.get("url", ""),
        }
        if entry.get("fetched_at") and hasattr(entry["fetched_at"], "isoformat"):
            d["fetched_at"] = entry["fetched_at"].isoformat()
        decks.append(d)

    decks.sort(key=lambda d: d["commander"].lower())
    return _json_response({"decks": decks})


def _load_deck_usage_for_user(user_id: int) -> dict[str, int]:
    """Retourne {card_name_lower: nb_decks_où_utilisée} depuis la DB."""
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal as _SL
    with _SL() as sess:
        rows = sess.execute(_t("""
            SELECT LOWER(TRIM(card_name)) AS cn, COUNT(DISTINCT commander) AS cnt
            FROM user_deck_cards
            WHERE user_id = :uid
            GROUP BY LOWER(TRIM(card_name))
        """), {"uid": user_id}).fetchall()
    return {r.cn: r.cnt for r in rows}


@router.get("/api/collection-commanders")
def api_collection_commanders(
    request: Request,
    top: int = Query(default=10, ge=1, le=50),
    mode: str = Query(default="available"),  # "available" | "all"
    staple_threshold: float = Query(default=40.0, ge=0.0, le=100.0),
) -> Response:
    """
    Retourne les `top` commandants pour lesquels la collection couvre
    la plus grande valeur (Cardmarket low_price) parmi leurs 100 cartes
    les plus jouées. Images Scryfall incluses.
    mode="available" : seulement les cartes non utilisées dans les decks
    mode="all"       : toute la collection sans filtre deck
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from sqlalchemy import text
    import json as _j
    try:
        from src.manamind.db.engine import SessionLocal
        _DB_AVAILABLE = SessionLocal is not None
    except Exception:
        SessionLocal = None
        _DB_AVAILABLE = False

    deck_usage = _load_deck_usage_for_user(user["id"]) if mode == "available" else {}

    with SessionLocal() as s:
        # Agréger par nom : une même carte peut exister en plusieurs exemplaires
        # (éditions, finitions) sur des lignes distinctes.
        collection_rows = s.execute(text("""
            SELECT LOWER(TRIM(card_name)) AS name, SUM(quantity) AS quantity
            FROM user_collection WHERE user_id = :uid
            GROUP BY 1
        """), {"uid": user["id"]}).fetchall()

        # Construire la collection selon le mode
        coll: dict[str, int] = {}
        for r in collection_rows:
            name_lower = r.name
            if mode == "available":
                # Une carte engagee dans un deck est ecartee entierement, meme
                # possedee en plusieurs exemplaires : l'ecran promet « les cartes
                # qui ne sont dans aucun de vos decks », et un deuxieme
                # exemplaire d'une carte deja jouee ne fonde pas un nouveau deck.
                if deck_usage.get(name_lower, 0) == 0:
                    coll[name_lower] = r.quantity
            else:
                coll[name_lower] = r.quantity

        # Les communes et peu communes des extensions cochees comme ouvertes
        # comptent comme disponibles : elles sont a portee de main sans avoir
        # ete saisies une par une. Zero exemplaire connu, donc elles ne
        # priment jamais sur une carte reellement en collection.
        from manamind.collection_advisor import load_opened_set_cards
        for opened_name in load_opened_set_cards(user["id"]).values():
            key = opened_name.strip().lower()
            if key not in coll and deck_usage.get(key, 0) == 0:
                coll[key] = 1

        if not coll:
            return _json_response({"commanders": []})

        # Terrains de base et cartes trop courantes sont ecartes, comme dans
        # « Trouver un nouveau commandant » : jouees partout, elles ne
        # distinguent aucun commandant et gonflaient le score de tous.
        neutral = {
            r.name for r in s.execute(text("""
                SELECT DISTINCT n.name
                FROM unnest(CAST(:names AS TEXT[])) AS n(name)
                LEFT JOIN deck_stat_global g
                       ON LOWER(BTRIM(g.card_name)) = n.name
                LEFT JOIN scryfall_cards sc
                       ON sc.normalized_name = mm_normalize_name(n.name)
                WHERE COALESCE(g.global_frequency, 0) > :threshold
                   OR sc.type_line ILIKE 'Basic Land%'
            """), {"names": list(coll.keys()),
                   "threshold": staple_threshold}).fetchall()
        }
        coll = {name: qty for name, qty in coll.items() if name not in neutral}
        if not coll:
            return _json_response({"commanders": []})

        # Préparer les données comme listes séparées pour unnest PostgreSQL (pas d'interpolation)
        coll_names = list(coll.keys())    # list[str] de noms normalisés
        coll_qtys  = list(coll.values())  # list[int] de quantités

        rows = s.execute(text("""
            WITH avail AS (
                SELECT unnest(CAST(:names AS TEXT[])) AS card_name_lower,
                       unnest(CAST(:qtys  AS INTEGER[])) AS qty
            ),
            top100 AS (
                -- Pre-filtre sur le taux d'inclusion : classer les 3,5 M lignes
                -- de deck_stat_commander pour n'en garder que 173 k coutait
                -- 1,3 s. Mesure sur la base : la 100e carte d'un commandant
                -- n'est jamais sous 6,56 % (1er centile a 18,8 %), donc le
                -- seuil a 1 % garde une marge de six et ne peut retirer aucune
                -- carte du classement.
                SELECT commander, card_name,
                       -- card_name departage les ex aequo : sans lui, deux
                       -- executions pouvaient retenir des 100es cartes
                       -- differentes et donner des totaux qui varient.
                       ROW_NUMBER() OVER (PARTITION BY commander
                                          ORDER BY inclusion_rate DESC, card_name) AS rk
                FROM deck_stat_commander
                WHERE inclusion_rate >= 1.0
            ),
            top100_filtered AS (
                SELECT commander, card_name FROM top100 WHERE rk <= 100
            ),
            collection_match AS (
                SELECT t.commander, t.card_name, a.qty AS qty
                FROM top100_filtered t
                JOIN avail a ON a.card_name_lower = LOWER(TRIM(t.card_name))
            ),
            -- Prix et illustrations restreints aux cartes de la collection :
            -- agreger la grille Cardmarket et le catalogue Scryfall en entier
            -- pour n'en garder que quelques milliers de lignes coutait 2,4 s.
            prices AS (
                -- Un LATERAL par produit a ete essaye pour eviter le parcours
                -- complet de la grille : le planificateur bascule alors sur un
                -- plan a plusieurs minutes. Le filtre par sous-requete reste le
                -- meilleur compromis mesure.
                SELECT cp.en_name AS card_name, MIN(pe.low_price) AS low_price
                FROM cardmarket_products cp
                JOIN cardmarket_price_guide_entries pe ON pe.id_product = cp.id_product
                WHERE pe.low_price IS NOT NULL AND pe.low_price > 0
                  AND LOWER(TRIM(cp.en_name)) IN (SELECT card_name_lower FROM avail)
                GROUP BY cp.en_name
            ),
            card_images AS (
                SELECT LOWER(TRIM(sc.name)) AS name_lower,
                       MIN(p.image_normal) AS image_url
                FROM scryfall_cards sc
                JOIN scryfall_card_printings p ON p.card_id = sc.id
                WHERE p.image_normal IS NOT NULL AND p.lang = 'en'
                  AND LOWER(TRIM(sc.name)) IN (SELECT card_name_lower FROM avail)
                GROUP BY LOWER(TRIM(sc.name))
            ),
            scored AS (
                SELECT cm.commander, cm.card_name, cm.qty,
                       COALESCE(pr.low_price, 0) AS low_price,
                       ci.image_url AS card_image
                FROM collection_match cm
                LEFT JOIN prices pr ON LOWER(TRIM(pr.card_name)) = LOWER(TRIM(cm.card_name))
                LEFT JOIN card_images ci ON ci.name_lower = LOWER(TRIM(cm.card_name))
            )
            -- L'illustration du commandant est resolue par _commander_images :
            -- le CTE qui s'en chargeait ici rescannait tout Scryfall une
            -- seconde fois, et laissait les duos « A & B » sans image.
            SELECT s.commander,
                   SUM(s.low_price) AS total_value,
                   COUNT(*) AS card_count,
                   JSON_AGG(
                       JSON_BUILD_OBJECT(
                           'card_name', s.card_name,
                           'low_price', ROUND(s.low_price::numeric, 2),
                           'qty', s.qty,
                           'image_url', s.card_image
                       ) ORDER BY s.low_price DESC
                   ) AS cards
            FROM scored s
            GROUP BY s.commander
            ORDER BY total_value DESC
            LIMIT :top
        """), {"names": coll_names, "qtys": coll_qtys, "top": top}).fetchall()

    with SessionLocal() as s:
        images = _commander_images(s, [r.commander for r in rows])

    result = []
    for r in rows:
        cards = r.cards if isinstance(r.cards, list) else _j.loads(r.cards)
        shots = images.get(r.commander) or []
        result.append({
            "commander": r.commander,
            "commander_image": shots[0] if shots else None,
            "commander_images": shots,
            "total_value": round(float(r.total_value or 0), 2),
            "card_count": r.card_count,
            "cards": cards,
        })
    return _json_response({"commanders": result})


def _commander_images(session, names: list[str]) -> dict[str, list[str]]:
    """Illustrations d'un commandant : une par carte, deux pour un duo.

    Les paires de partenaires sont stockees « A & B » et ne correspondent a
    aucune carte de ce nom : sans decoupage, la moitie des suggestions
    s'affichait sans illustration.
    """
    from sqlalchemy import text

    parts: dict[str, list[str]] = {n: [p.strip() for p in n.split(" & ") if p.strip()]
                                   for n in names}
    wanted = sorted({p for pieces in parts.values() for p in pieces})
    if not wanted:
        return {}

    rows = session.execute(text("""
        SELECT n.name AS asked, img.image_url
        FROM unnest(CAST(:names AS TEXT[])) AS n(name)
        LEFT JOIN LATERAL (
            SELECT MIN(p.image_normal) AS image_url
            FROM scryfall_cards sc
            JOIN scryfall_card_printings p ON p.card_id = sc.id
            WHERE sc.normalized_name = mm_normalize_name(n.name)
              AND p.image_normal IS NOT NULL AND p.lang = 'en'
        ) img ON TRUE
    """), {"names": wanted}).fetchall()
    found = {r.asked: r.image_url for r in rows if r.image_url}

    return {name: [found[p] for p in pieces if p in found]
            for name, pieces in parts.items()}


@router.get("/api/v2/commander-build/{commander}")
def api_commander_build(
    commander: str,
    request: Request,
    staple_threshold: float = Query(default=40.0, ge=0.0, le=100.0),
) -> Response:
    """Detail d'un commandant a construire : cartes possedees et a acheter.

    Ecarte les memes cartes que la liste dont cette page est le detail :
    terrains de base et cartes jouees dans plus de staple_threshold % des
    decks, qui ne distinguent aucun commandant.

    Les cartes possedees reprennent le calcul de /api/collection-commanders ;
    les manquantes sont les plus jouees du commandant qui n'y figurent pas,
    classees par taux d'inclusion — ce sont celles qui completent le deck.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from sqlalchemy import text
    from manamind.db.engine import SessionLocal

    with SessionLocal() as s:
        collection = {
            r.name: int(r.quantity)
            for r in s.execute(text("""
                SELECT LOWER(TRIM(card_name)) AS name, SUM(quantity) AS quantity
                FROM user_collection WHERE user_id = :uid
                GROUP BY 1
            """), {"uid": user["id"]}).fetchall()
        }

        # deck_stat_commander fait 3,5 M lignes : la comparaison doit rester
        # nue pour que ix_deck_stat_commander_commander serve. Le nom vient de
        # nos propres donnees, donc l'egalite exacte suffit presque toujours ;
        # le repli couvre une URL saisie a la main.
        top = s.execute(text("""
            SELECT dsc.card_name, dsc.inclusion_rate
            FROM deck_stat_commander dsc
            WHERE dsc.commander = :cmd
              AND COALESCE((
                    SELECT g.global_frequency FROM deck_stat_global g
                    WHERE LOWER(BTRIM(g.card_name)) = LOWER(BTRIM(dsc.card_name))
                    LIMIT 1
                  ), 0) <= :threshold
              AND NOT EXISTS (
                    SELECT 1 FROM scryfall_cards sc
                    WHERE sc.normalized_name = mm_normalize_name(dsc.card_name)
                      AND sc.type_line ILIKE 'Basic Land%'
                  )
            ORDER BY dsc.inclusion_rate DESC, dsc.card_name
            LIMIT 100
        """), {"cmd": commander, "threshold": staple_threshold}).fetchall()
        if not top:
            top = s.execute(text("""
                SELECT dsc.card_name, dsc.inclusion_rate
                FROM deck_stat_commander dsc
                WHERE LOWER(TRIM(dsc.commander)) = LOWER(TRIM(:cmd))
              AND COALESCE((
                    SELECT g.global_frequency FROM deck_stat_global g
                    WHERE LOWER(BTRIM(g.card_name)) = LOWER(BTRIM(dsc.card_name))
                    LIMIT 1
                  ), 0) <= :threshold
              AND NOT EXISTS (
                    SELECT 1 FROM scryfall_cards sc
                    WHERE sc.normalized_name = mm_normalize_name(dsc.card_name)
                      AND sc.type_line ILIKE 'Basic Land%'
                  )
                ORDER BY dsc.inclusion_rate DESC, dsc.card_name
                LIMIT 100
            """), {"cmd": commander, "threshold": staple_threshold}).fetchall()

        names = [r.card_name for r in top]
        # Prix et illustration en une passe, sur les 100 noms exacts : les deux
        # tables sont indexees sur ces colonnes, a condition de ne pas les
        # envelopper dans une fonction.
        extra = {}
        if names:
            extra = {
                r.card_name: r for r in s.execute(text("""
                    SELECT n.card_name,
                           ROUND(pr.low_price::numeric, 2) AS low_price,
                           img.image_url
                    FROM unnest(CAST(:names AS TEXT[])) AS n(card_name)
                    LEFT JOIN LATERAL (
                        SELECT MIN(pe.low_price) AS low_price
                        FROM cardmarket_products cp
                        JOIN cardmarket_price_guide_entries pe ON pe.id_product = cp.id_product
                        WHERE cp.en_name = n.card_name
                          AND pe.low_price IS NOT NULL AND pe.low_price > 0
                    ) pr ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT MIN(p.image_normal) AS image_url
                        FROM scryfall_cards sc
                        JOIN scryfall_card_printings p ON p.card_id = sc.id
                        WHERE sc.normalized_name = mm_normalize_name(n.card_name)
                          AND p.image_normal IS NOT NULL AND p.lang = 'en'
                    ) img ON TRUE
                """), {"names": names}).fetchall()
            }

        cmd_images = _commander_images(s, [commander]).get(commander, [])

    if not top:
        return _json_response({"error": "Commandant inconnu"}, status_code=404)

    owned, missing = [], []
    for r in top:
        info = extra.get(r.card_name)
        entry = {
            "card_name": r.card_name,
            "inclusion_rate": round(float(r.inclusion_rate or 0), 1),
            "low_price": float(info.low_price or 0) if info and info.low_price else 0.0,
            "image_url": info.image_url if info else None,
        }
        qty = collection.get((r.card_name or "").strip().lower(), 0)
        if qty:
            owned.append({**entry, "qty": qty})
        else:
            missing.append(entry)

    return _json_response({
        "commander": commander,
        "commander_image": cmd_images[0] if cmd_images else None,
        "commander_images": cmd_images,
        "owned": owned,
        "owned_value": round(sum(c["low_price"] for c in owned), 2),
        "missing": missing[:20],
        "missing_value": round(sum(c["low_price"] for c in missing[:20]), 2),
    })


@router.get("/api/collection")
def api_collection_list(
    request: Request,
    search: str = Query(""),
    sort: str = Query("name"),
    in_deck: str = Query(""),
) -> JSONResponse:
    """Liste les cartes de la collection avec filtre, tri et info decks."""
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    from collections import defaultdict

    # Construire cards_in_decks avec un seul JOIN au lieu de N requêtes par deck
    with SessionLocal() as sess:
        dc_rows = sess.execute(_t("""
            SELECT dc.card_name, d.commander
            FROM user_moxfield_decks d
            JOIN user_deck_cards dc
              ON dc.user_id = d.user_id
             AND LOWER(TRIM(dc.commander)) = LOWER(TRIM(d.commander))
            WHERE d.user_id = :uid
        """), {"uid": user["id"]}).fetchall()
    cards_in_decks: dict[str, list[str]] = defaultdict(list)
    for dc_row in dc_rows:
        if dc_row.card_name and dc_row.commander:
            cards_in_decks[dc_row.card_name.strip().lower()].append(dc_row.commander)

    with SessionLocal() as sess:
        rows = sess.execute(_t("""
            WITH best_print AS (
                SELECT DISTINCT ON (LOWER(TRIM(sc2.name)))
                       LOWER(TRIM(sc2.name)) AS name_lower,
                       p.scryfall_id,
                       p.image_normal
                FROM scryfall_card_printings p
                JOIN scryfall_cards sc2 ON sc2.id = p.card_id
                WHERE p.lang = 'en' AND p.image_normal IS NOT NULL
                ORDER BY LOWER(TRIM(sc2.name)), p.released_at DESC
            )
            SELECT uc.card_name, uc.quantity,
                   sc.type_line, sc.mana_cost, sc.mana_value,
                   bp.image_normal AS image_url,
                   bp.scryfall_id
            FROM user_collection uc
            LEFT JOIN scryfall_cards sc ON LOWER(TRIM(sc.name)) = LOWER(TRIM(uc.card_name))
            LEFT JOIN best_print bp ON bp.name_lower = LOWER(TRIM(uc.card_name))
            WHERE uc.user_id = :uid
              AND (:search = '' OR LOWER(uc.card_name) LIKE LOWER(:search_like))
            ORDER BY uc.card_name ASC
        """), {"uid": user["id"], "search": search, "search_like": f"%{search}%"}).fetchall()

    result = []
    for r in rows:
        norm = r.card_name.strip().lower()
        decks_containing = cards_in_decks.get(norm, [])
        has_deck = len(decks_containing) > 0

        if in_deck == "yes" and not has_deck:
            continue
        if in_deck == "no" and has_deck:
            continue

        result.append({
            "card_name": r.card_name,
            "quantity": r.quantity,
            "type_line": r.type_line or "",
            "mana_cost": r.mana_cost or "",
            "mana_value": r.mana_value,
            "image_url": r.image_url,
            "scryfall_id": r.scryfall_id,
            "in_decks": decks_containing,
        })

    if sort == "name":
        result.sort(key=lambda x: x["card_name"].lower())

    return _json_response({"cards": result, "total": len(result)})


@router.patch("/api/collection/{card_name}")
async def api_collection_update(card_name: str, request: Request) -> JSONResponse:
    """Met à jour la quantité d'une carte dans la collection."""
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    body = await request.json()
    qty = int(body.get("quantity", 0))

    with SessionLocal() as sess:
        if qty <= 0:
            sess.execute(_t("DELETE FROM user_collection WHERE user_id = :uid AND LOWER(TRIM(card_name)) = LOWER(TRIM(:name))"), {"uid": user["id"], "name": card_name})
        else:
            sess.execute(_t("UPDATE user_collection SET quantity = :qty WHERE user_id = :uid AND LOWER(TRIM(card_name)) = LOWER(TRIM(:name))"), {"qty": qty, "uid": user["id"], "name": card_name})
        sess.commit()

    return _json_response({"ok": True, "card_name": card_name, "quantity": qty})


@router.delete("/api/collection")
def api_collection_clear(request: Request) -> JSONResponse:
    """Vide entièrement la collection de l'utilisateur."""
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    with SessionLocal() as sess:
        sess.execute(_t("DELETE FROM user_collection WHERE user_id = :uid"), {"uid": user["id"]})
        sess.commit()
    return _json_response({"ok": True})


@router.delete("/api/collection/{card_name}")
def api_collection_delete(card_name: str, request: Request) -> JSONResponse:
    """Supprime une carte de la collection."""
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal

    with SessionLocal() as sess:
        sess.execute(_t("DELETE FROM user_collection WHERE user_id = :uid AND LOWER(TRIM(card_name)) = LOWER(TRIM(:name))"), {"uid": user["id"], "name": card_name})
        sess.commit()

    return _json_response({"ok": True})


@router.get("/api/card-inclusion")
def api_card_inclusion(
    card: str = Query(...),
    commander: str = Query(...),
) -> JSONResponse:
    """Taux d'inclusion d'une carte pour un commandant donné."""
    from manamind.card_commander_matcher import _normalize, _load_frequency_index
    idx = _load_frequency_index()
    cmd_norm  = _normalize(commander)
    card_norm = _normalize(card)
    cmd_data  = idx.get(cmd_norm, {})
    entry     = cmd_data.get(card_norm)
    if entry is None:
        return _json_response({"inclusion_rate": None})
    return _json_response({
        "inclusion_rate": round(entry["inclusion_rate"], 1),
        "decks_with_card": entry["decks_with_card"],
        "total_decks": entry["total_decks"],
    })


@router.get("/api/commander-suggest")
def api_commander_suggest(
    card: str = Query(..., description="Nom de la carte à rechercher"),
    top: int = Query(default=3, ge=1, le=10),
    mode: str = Query(default="mine", description="'mine' = mes commandants, 'all' = tous"),
) -> JSONResponse:
    """
    mode='mine' : parmi les commandants de data/My_commanders.txt (comportement original)
    mode='all'  : tous les commandants de la DB, avec flag in_my_decks
    """
    from manamind.card_commander_matcher import suggest_commanders, load_allowed_commanders
    from sqlalchemy import text as _text
    try:
        from src.manamind.db.engine import SessionLocal
        _DB_AVAILABLE = SessionLocal is not None
    except Exception:
        SessionLocal = None
        _DB_AVAILABLE = False

    card = card.strip()
    if not card:
        return _json_response({"error": "Paramètre 'card' manquant"}, status_code=400)

    if mode == "mine":
        results = suggest_commanders(card, top_n=top)
        return _json_response({"card": card, "mode": "mine", "suggestions": results})

    # mode == "all" : requête globale sans filtre commandant
    import unicodedata as _ud

    def _norm(s: str) -> str:
        s = _ud.normalize("NFKD", s)
        return "".join(c for c in s if not _ud.combining(c)).lower().strip()

    my_commanders = {_norm(c) for c in load_allowed_commanders()}

    with SessionLocal() as session:
        rows = session.execute(_text("""
            SELECT dsc.commander, dsc.inclusion_rate, dsc.decks_with_card, dsc.total_decks,
                   (
                     SELECT MIN(p2.image_normal)
                     FROM scryfall_cards sc2
                     JOIN scryfall_card_printings p2
                       ON p2.card_id = sc2.id AND p2.lang = 'en' AND p2.image_normal IS NOT NULL
                     WHERE LOWER(TRIM(sc2.name)) = LOWER(TRIM(
                       SPLIT_PART(dsc.commander, ' & ', 1)
                     ))
                   ) AS image_url
            FROM deck_stat_commander dsc
            WHERE dsc.card_name = :card
            GROUP BY dsc.commander, dsc.inclusion_rate, dsc.decks_with_card, dsc.total_decks
            ORDER BY dsc.inclusion_rate DESC
            LIMIT :top
        """), {"card": card, "top": top}).fetchall()

    suggestions = []
    for row in rows:
        suggestions.append({
            "commander":       row.commander,
            "inclusion_rate":  round(row.inclusion_rate, 2),
            "decks_with_card": row.decks_with_card,
            "total_decks":     row.total_decks,
            "image_url":       row.image_url,
            "in_my_decks":     _norm(row.commander) in my_commanders,
        })

    return _json_response({"card": card, "mode": "all", "suggestions": suggestions})
