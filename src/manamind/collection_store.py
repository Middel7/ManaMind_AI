"""Acces a la collection utilisateur — modele par exemplaire.

Un exemplaire = une carte dans une edition, une finition, une langue et un etat
donnes. Les analyses (recommandations, matching de decks) continuent d'agreger
par nom de carte : l'exemplaire ne sert qu'a l'affichage et a la valorisation.

Resolution de l'impression, par ordre de fiabilite :
  1. scryfall_id de l'exemplaire (index unique)
  2. edition + numero de collecteur
  3. impression la plus recente portant ce nom
"""

from __future__ import annotations

import time as _time
from datetime import datetime
from typing import Any

from sqlalchemy import text

from manamind.db.engine import SessionLocal

# Cache des agregats du dashboard : { user_id: (timestamp, stats) }
_STATS_CACHE: dict[int, tuple[float, dict]] = {}
_STATS_TTL = 60.0

# ── Constantes ────────────────────────────────────────────────────────────────

FINISHES = ("nonfoil", "foil", "etched")
CONDITIONS = ("NM", "EX", "GD", "LP", "PL", "PO")
DEFAULT_LANGUAGE = "en"

SORTS = {
    "name": "card_name ASC",
    "name_desc": "card_name DESC",
    "recent": "added_at DESC NULLS LAST, id DESC",
    "oldest": "added_at ASC NULLS LAST, id ASC",
    "quantity": "quantity DESC, card_name ASC",
    "value": "unit_price DESC NULLS LAST, card_name ASC",
    "mana": "mana_value ASC NULLS LAST, card_name ASC",
    "mana_desc": "mana_value DESC NULLS LAST, card_name ASC",
    "rarity": "rarity_rank DESC NULLS LAST, card_name ASC",
}

# Les editions Secret Lair (codes SL*) sont ecartees de tout choix
# d'illustration : leurs visuels alternatifs ne representent pas la carte.
NO_SECRET_LAIR = "{alias}.set_code NOT ILIKE 'sl%%'"

# Bloc SQL commun : resout impression, carte, prix et extension d'un exemplaire.
_ENRICH_SQL = """
    -- Impression exacte de l'exemplaire (renseignee a l'ajout)
    LEFT JOIN scryfall_card_printings pd
           ON pd.id = uc.printing_id AND pd.set_code NOT ILIKE 'sl%'

    -- Repli : impression illustrative, quand l'edition n'est pas connue —
    -- ou quand celle qui est enregistree est un Secret Lair.
    LEFT JOIN LATERAL (
        SELECT p.id, p.scryfall_id, p.set_code, p.collector_number, p.rarity,
               p.image_small, p.image_normal, p.scryfall_uri, p.artist,
               p.cardmarket_id
        FROM scryfall_card_printings p
        WHERE pd.id IS NULL
          AND p.card_id = uc.card_id AND p.lang = 'en'
        -- Les Secret Lair passent en dernier plutot que d'etre exclues : une
        -- poignee de cartes n'existent que la, et resteraient sans visuel.
        ORDER BY (p.set_code NOT ILIKE 'sl%') DESC,
                 (p.image_normal IS NOT NULL) DESC, p.released_at DESC NULLS LAST
        LIMIT 1
    ) pf ON TRUE

    LEFT JOIN scryfall_cards sc ON sc.id = uc.card_id

    -- Prix de reference du projet : le low_price Cardmarket de l'edition la
    -- moins chere, precalcule par la vue card_min_price. Il ne depend ni de
    -- l'edition possedee ni de la finition.
    LEFT JOIN card_min_price cm ON cm.card_id = uc.card_id

    LEFT JOIN scryfall_mtg_sets st
           ON UPPER(st.code) = UPPER(COALESCE(pd.set_code, pf.set_code, uc.set_code))
"""

# Prix unitaire : low_price Cardmarket de l'edition la moins chere.
_UNIT_PRICE_SQL = """
    cm.low_price
"""

_RARITY_RANK_SQL = """
    CASE COALESCE(pd.rarity, pf.rarity)
        WHEN 'mythic' THEN 4 WHEN 'rare' THEN 3
        WHEN 'uncommon' THEN 2 WHEN 'common' THEN 1 ELSE 0 END
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _key(value: str | None) -> str:
    """Cle de rapprochement d'un nom de carte : face avant, sans diacritiques.

    Les decklists ne citent que la face avant des cartes recto-verso, alors que
    scryfall_cards porte le nom complet : sans cela, une carte presente dans un
    deck apparaitrait comme inutilisee.
    """
    from mtgdb.db.models.card import normalize_card_name
    return normalize_card_name(value or "").split(" // ")[0].strip()


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _clean_finish(value: str | None) -> str:
    finish = (value or "nonfoil").strip().lower()
    return finish if finish in FINISHES else "nonfoil"


def _row_to_item(row: Any) -> dict:
    """Serialise une ligne enrichie en exemplaire pour le front."""
    unit = float(row.unit_price) if row.unit_price is not None else None
    return {
        "id": row.id,
        "card_name": row.card_name,
        "quantity": row.quantity,
        "set_code": row.set_code,
        "set_name": row.set_name,
        "set_icon": row.set_icon,
        "collector_number": row.collector_number,
        "finish": row.finish,
        "language": row.language,
        "condition": row.condition,
        "location": row.location,
        "note": row.note,
        "scryfall_id": row.scryfall_id,
        "scryfall_uri": row.scryfall_uri,
        "rarity": row.rarity,
        "artist": row.artist,
        "image_small": row.image_small,
        "image_normal": row.image_normal,
        "type_line": row.type_line or "",
        "mana_cost": row.mana_cost or "",
        "mana_value": row.mana_value,
        "colors": list(row.colors or []),
        "color_identity": list(row.color_identity or []),
        "oracle_text": row.oracle_text or "",
        "game_changer": bool(row.game_changer),
        "unit_price": unit,
        "total_price": round(unit * row.quantity, 2) if unit is not None else None,
        "added_at": _iso(row.added_at),
        "updated_at": _iso(row.updated_at),
        "in_decks": [],
    }


def _deck_usage(session: Any, user_id: int) -> dict[str, list[str]]:
    """{ nom normalise: [commandants des decks qui l'utilisent] }

    Le rapprochement se fait par deck_id : joindre par commandant confondait deux
    decks qui partagent le meme, et n'en comptait alors qu'un.
    """
    rows = session.execute(text("""
        SELECT DISTINCT dc.card_name, dc.deck_id, d.commander
        FROM user_moxfield_decks d
        JOIN user_deck_cards dc
          ON dc.user_id = d.user_id AND dc.deck_id = d.deck_id
        WHERE d.user_id = :uid
    """), {"uid": user_id}).fetchall()
    usage: dict[str, list[str]] = {}
    for row in rows:
        if row.card_name and row.commander:
            usage.setdefault(_key(row.card_name), []).append(row.commander)
    return usage


# ── Lecture ───────────────────────────────────────────────────────────────────

def list_items(
    user_id: int,
    *,
    search: str = "",
    colors: list[str] | None = None,
    types: list[str] | None = None,
    rarities: list[str] | None = None,
    sets: list[str] | None = None,
    finishes: list[str] | None = None,
    in_deck: str = "",
    sort: str = "name",
    limit: int = 60,
    offset: int = 0,
) -> dict:
    """Liste paginee des exemplaires, avec filtres et usage en deck."""
    where = ["uc.user_id = :uid"]
    params: dict[str, Any] = {"uid": user_id}

    if search:
        # Le nom stocke est l'anglais : sans la seconde branche, chercher
        # « Oiseaux de paradis » ne trouvait pas Birds of Paradise pourtant
        # presente. ILIKE, et non LOWER(...) LIKE : c'est l'index trigram de
        # printed_name qui repond (la base est en collation French_France).
        where.append("""(
            LOWER(uc.card_name) LIKE :search
            OR EXISTS (
                SELECT 1 FROM scryfall_card_printings tr
                WHERE tr.card_id = uc.card_id
                  AND tr.printed_name ILIKE :search
            )
        )""")
        params["search"] = f"%{search.strip().lower()}%"
    if sets:
        where.append("UPPER(COALESCE(pd.set_code, pf.set_code, uc.set_code)) = ANY(:sets)")
        params["sets"] = [s.upper() for s in sets]
    if finishes:
        where.append("uc.finish = ANY(:finishes)")
        params["finishes"] = [_clean_finish(f) for f in finishes]
    if rarities:
        where.append("COALESCE(pd.rarity, pf.rarity) = ANY(:rarities)")
        params["rarities"] = [r.lower() for r in rarities]
    if colors:
        # "C" = incolore : identite de couleur vide
        wanted = [c.upper() for c in colors if c.upper() in ("W", "U", "B", "R", "G", "C")]
        clauses = []
        if any(c != "C" for c in wanted):
            clauses.append("sc.color_identity && :colors")
            params["colors"] = [c for c in wanted if c != "C"]
        if "C" in wanted:
            clauses.append("(sc.color_identity IS NULL OR cardinality(sc.color_identity) = 0)")
        if clauses:
            where.append("(" + " OR ".join(clauses) + ")")
    if in_deck in ("yes", "no"):
        clause = """
            EXISTS (
                SELECT 1 FROM user_deck_cards dc
                WHERE dc.user_id = uc.user_id
                  AND split_part(mm_normalize_name(dc.card_name), ' // ', 1)
                      = split_part(COALESCE(sc.normalized_name,
                                            mm_normalize_name(uc.card_name)), ' // ', 1)
            )
        """
        where.append(clause if in_deck == "yes" else f"NOT {clause}")
    if types:
        type_clauses = []
        for idx, type_name in enumerate(types):
            key = f"type{idx}"
            type_clauses.append(f"sc.type_line ILIKE :{key}")
            params[key] = f"%{type_name}%"
        if type_clauses:
            where.append("(" + " OR ".join(type_clauses) + ")")

    order = SORTS.get(sort, SORTS["name"])
    where_sql = " AND ".join(where)

    query = f"""
        SELECT uc.id, uc.card_name, uc.quantity, uc.finish, uc.language,
               uc.condition, uc.location, uc.note, uc.added_at, uc.updated_at,
               COALESCE(pd.scryfall_id, pf.scryfall_id)           AS scryfall_id,
               UPPER(COALESCE(pd.set_code, pf.set_code, uc.set_code)) AS set_code,
               COALESCE(pd.collector_number, pf.collector_number,
                        uc.collector_number)                       AS collector_number,
               COALESCE(pd.rarity, pf.rarity)                     AS rarity,
               COALESCE(pd.image_small, pf.image_small)           AS image_small,
               COALESCE(pd.image_normal, pf.image_normal)         AS image_normal,
               COALESCE(pd.scryfall_uri, pf.scryfall_uri)         AS scryfall_uri,
               COALESCE(pd.artist, pf.artist)                     AS artist,
               st.name                                            AS set_name,
               st.icon_svg_uri                                    AS set_icon,
               sc.type_line, sc.mana_cost, sc.mana_value, sc.colors,
               sc.color_identity, sc.oracle_text,
               COALESCE(sc.game_changer, false)                   AS game_changer,
               {_UNIT_PRICE_SQL} AS unit_price,
               {_RARITY_RANK_SQL} AS rarity_rank,
               COUNT(*) OVER () AS total_count
        FROM user_collection uc
        {_ENRICH_SQL}
        WHERE {where_sql}
        ORDER BY {order}
        LIMIT :limit OFFSET :offset
    """
    params["limit"] = max(1, min(limit, 500))
    params["offset"] = max(0, offset)

    with SessionLocal() as session:
        rows = session.execute(text(query), params).fetchall()
        usage = _deck_usage(session, user_id)

    total = rows[0].total_count if rows else 0
    items = []
    for row in rows:
        item = _row_to_item(row)
        item["in_decks"] = usage.get(_key(row.card_name), [])
        items.append(item)

    return {"items": items, "total": total, "limit": params["limit"], "offset": params["offset"]}


def facets(user_id: int) -> dict:
    """Valeurs disponibles pour les filtres, avec leur effectif.

    Une seule passe d'enrichissement alimente les cinq facettes.
    """
    enriched = """
        WITH enriched AS (
            SELECT UPPER(COALESCE(pd.set_code, pf.set_code, uc.set_code)) AS set_code,
                   COALESCE(pd.rarity, pf.rarity) AS rarity,
                   uc.finish,
                   sc.color_identity,
                   sc.type_line
            FROM user_collection uc
            LEFT JOIN scryfall_card_printings pd ON pd.id = uc.printing_id
            LEFT JOIN LATERAL (
                SELECT p.set_code, p.rarity
                FROM scryfall_card_printings p
                WHERE uc.printing_id IS NULL
                  AND p.card_id = uc.card_id AND p.lang = 'en'
                ORDER BY p.released_at DESC NULLS LAST LIMIT 1
            ) pf ON TRUE
            LEFT JOIN scryfall_cards sc ON sc.id = uc.card_id
            WHERE uc.user_id = :uid
        )
    """

    with SessionLocal() as session:
        sets = session.execute(text(enriched + """
            SELECT e.set_code AS code, MAX(st.name) AS name, COUNT(*) AS n
            FROM enriched e
            LEFT JOIN scryfall_mtg_sets st ON UPPER(st.code) = e.set_code
            WHERE e.set_code IS NOT NULL
            GROUP BY 1 ORDER BY n DESC LIMIT 60
        """), {"uid": user_id}).fetchall()

        rarities = session.execute(text(enriched + """
            SELECT rarity, COUNT(*) AS n FROM enriched
            WHERE rarity IS NOT NULL GROUP BY 1
        """), {"uid": user_id}).fetchall()

        finishes = session.execute(text("""
            SELECT finish, COUNT(*) AS n FROM user_collection
            WHERE user_id = :uid GROUP BY 1
        """), {"uid": user_id}).fetchall()

        colors = session.execute(text(enriched + """
            SELECT c.color, COUNT(*) AS n
            FROM enriched e
            CROSS JOIN LATERAL unnest(
                CASE WHEN COALESCE(cardinality(e.color_identity), 0) = 0 THEN ARRAY['C']
                     ELSE e.color_identity END
            ) AS c(color)
            GROUP BY 1
        """), {"uid": user_id}).fetchall()

        types = session.execute(text(enriched + """
            SELECT t.label, COUNT(*) AS n
            FROM enriched e
            CROSS JOIN LATERAL (
                SELECT unnest(ARRAY[
                    'Creature', 'Instant', 'Sorcery', 'Artifact',
                    'Enchantment', 'Planeswalker', 'Land', 'Battle'
                ]) AS label
            ) t
            WHERE e.type_line ILIKE '%' || t.label || '%'
            GROUP BY 1 ORDER BY 2 DESC
        """), {"uid": user_id}).fetchall()

    return {
        "sets": [{"code": r.code, "name": r.name or r.code, "count": r.n} for r in sets],
        "rarities": [{"value": r.rarity, "count": r.n} for r in rarities],
        "finishes": [{"value": r.finish, "count": r.n} for r in finishes],
        "colors": [{"value": r.color, "count": r.n} for r in colors],
        "types": [{"value": r.label, "count": r.n} for r in types],
    }


def stats(user_id: int, *, use_cache: bool = True) -> dict:
    """Agregats du tableau de bord.

    La valorisation croise 9,5 M de lignes de prix : on la garde en cache une
    minute, les cours ne bougeant pas a la seconde.
    """
    if use_cache:
        cached = _STATS_CACHE.get(user_id)
        if cached and (_time.time() - cached[0]) < _STATS_TTL:
            return cached[1]

    result = _compute_stats(user_id)
    _STATS_CACHE[user_id] = (_time.time(), result)
    return result


def invalidate_stats(user_id: int) -> None:
    """A appeler apres toute ecriture en collection."""
    _STATS_CACHE.pop(user_id, None)


def _compute_stats(user_id: int) -> dict:
    with SessionLocal() as session:
        base = session.execute(text("""
            SELECT COUNT(*)                                   AS lines,
                   COALESCE(SUM(quantity), 0)                 AS copies,
                   COUNT(DISTINCT LOWER(TRIM(card_name)))     AS distinct_cards,
                   COUNT(*) FILTER (WHERE finish <> 'nonfoil') AS foils,
                   MAX(updated_at)                            AS last_update,
                   MIN(added_at)                              AS first_add
            FROM user_collection WHERE user_id = :uid
        """), {"uid": user_id}).fetchone()

        value = session.execute(text(f"""
            SELECT COALESCE(SUM({_UNIT_PRICE_SQL} * uc.quantity), 0) AS total,
                   COUNT(*) FILTER (WHERE {_UNIT_PRICE_SQL} IS NULL) AS unpriced
            FROM user_collection uc
            {_ENRICH_SQL}
            WHERE uc.user_id = :uid
        """), {"uid": user_id}).fetchone()

        decks = session.execute(text("""
            SELECT COUNT(*) AS n, MAX(COALESCE(fetched_at, created_at)) AS last_deck
            FROM user_moxfield_decks WHERE user_id = :uid
        """), {"uid": user_id}).fetchone()

        recent = session.execute(text("""
            SELECT COUNT(*) AS n FROM user_collection
            WHERE user_id = :uid AND added_at > NOW() - INTERVAL '30 days'
        """), {"uid": user_id}).scalar()

        # Cartes de la collection qu'aucun deck n'utilise
        dormant = session.execute(text("""
            SELECT COUNT(*)
            FROM user_collection uc
            LEFT JOIN scryfall_cards sc ON sc.id = uc.card_id
            WHERE uc.user_id = :uid
              AND NOT EXISTS (
                  SELECT 1 FROM user_deck_cards dc
                  WHERE dc.user_id = uc.user_id
                    AND split_part(mm_normalize_name(dc.card_name), ' // ', 1)
                        = split_part(COALESCE(sc.normalized_name,
                                              mm_normalize_name(uc.card_name)), ' // ', 1)
              )
        """), {"uid": user_id}).scalar()

        top = session.execute(text(f"""
            SELECT uc.card_name, uc.quantity, uc.finish,
                   UPPER(COALESCE(pd.set_code, pf.set_code, uc.set_code)) AS set_code,
                   COALESCE(pd.image_normal, pf.image_normal)      AS image_normal,
                   COALESCE(pd.scryfall_id, pf.scryfall_id)        AS scryfall_id,
                   {_UNIT_PRICE_SQL} AS unit_price
            FROM user_collection uc
            {_ENRICH_SQL}
            WHERE uc.user_id = :uid AND {_UNIT_PRICE_SQL} IS NOT NULL
            ORDER BY {_UNIT_PRICE_SQL} DESC
            LIMIT 6
        """), {"uid": user_id}).fetchall()

    return {
        "lines": base.lines,
        "copies": int(base.copies),
        "distinct_cards": base.distinct_cards,
        "foils": base.foils,
        "decks": decks.n,
        "added_last_30d": recent or 0,
        "dormant": dormant or 0,
        "value_eur": round(float(value.total or 0), 2),
        "unpriced": value.unpriced or 0,
        "last_update": _iso(base.last_update),
        "first_add": _iso(base.first_add),
        "last_deck": _iso(decks.last_deck),
        "top_cards": [
            {
                "card_name": r.card_name,
                "quantity": r.quantity,
                "finish": r.finish,
                "set_code": r.set_code,
                "image_normal": r.image_normal,
                "scryfall_id": r.scryfall_id,
                "unit_price": float(r.unit_price) if r.unit_price is not None else None,
            }
            for r in top
        ],
    }


def recent_items(user_id: int, limit: int = 12) -> list[dict]:
    """Derniers exemplaires ajoutes, pour le fil d'activite."""
    return list_items(user_id, sort="recent", limit=limit)["items"]


def dormant_items(user_id: int, limit: int = 24) -> list[dict]:
    """Cartes possedees qu'aucun deck n'utilise, les plus cheres d'abord."""
    # Les noms utilises en deck sont normalises et dedupliques une seule fois :
    # en NOT EXISTS correle, la comparaison enveloppait mm_normalize_name() des
    # deux cotes, ce qui interdisait le hash join et forcait un produit
    # collection x cartes de decks (~2,7 M comparaisons, 2,3 s).
    query = f"""
        WITH used AS (
            SELECT DISTINCT split_part(mm_normalize_name(dc.card_name), ' // ', 1) AS key
            FROM user_deck_cards dc
            WHERE dc.user_id = :uid
        )
        SELECT uc.id, uc.card_name, uc.quantity, uc.finish, uc.language,
               uc.condition, uc.location, uc.note, uc.added_at, uc.updated_at,
               COALESCE(pd.scryfall_id, pf.scryfall_id)        AS scryfall_id,
               UPPER(COALESCE(pd.set_code, pf.set_code, uc.set_code)) AS set_code,
               COALESCE(pd.collector_number, pf.collector_number,
                        uc.collector_number)                    AS collector_number,
               COALESCE(pd.rarity, pf.rarity)                  AS rarity,
               COALESCE(pd.image_small, pf.image_small)        AS image_small,
               COALESCE(pd.image_normal, pf.image_normal)      AS image_normal,
               COALESCE(pd.scryfall_uri, pf.scryfall_uri)      AS scryfall_uri,
               COALESCE(pd.artist, pf.artist)                  AS artist,
               st.name AS set_name, st.icon_svg_uri AS set_icon,
               sc.type_line, sc.mana_cost, sc.mana_value, sc.colors,
               sc.color_identity, sc.oracle_text,
               COALESCE(sc.game_changer, false) AS game_changer,
               {_UNIT_PRICE_SQL} AS unit_price
        FROM user_collection uc
        {_ENRICH_SQL}
        LEFT JOIN used ON used.key = split_part(COALESCE(sc.normalized_name,
                              mm_normalize_name(uc.card_name)), ' // ', 1)
        WHERE uc.user_id = :uid
          AND COALESCE(pd.rarity, pf.rarity) IN ('rare', 'mythic')
          AND used.key IS NULL
        ORDER BY {_UNIT_PRICE_SQL} DESC NULLS LAST
        LIMIT :limit
    """
    with SessionLocal() as session:
        rows = session.execute(text(query), {"uid": user_id, "limit": limit}).fetchall()
    return [_row_to_item(r) for r in rows]


# ── Ecriture ──────────────────────────────────────────────────────────────────

def _resolve_printing(session: Any, name: str, set_code: str | None,
                      collector_number: str | None) -> dict:
    """Identifie l'impression et la carte d'un exemplaire.

    Materialiser card_id / printing_id des l'ajout evite de resoudre le nom a
    chaque lecture de la collection.
    """
    if set_code and collector_number:
        row = session.execute(text("""
            SELECT p.id, p.card_id, p.scryfall_id, p.set_code, p.collector_number
            FROM scryfall_card_printings p
            WHERE UPPER(p.set_code) = UPPER(:set) AND p.collector_number = :num
              AND p.lang = 'en'
            LIMIT 1
        """), {"set": set_code, "num": collector_number}).fetchone()
        if row:
            return {
                "printing_id": row.id, "card_id": row.card_id,
                "scryfall_id": row.scryfall_id,
                "set_code": row.set_code.upper(),
                "collector_number": row.collector_number,
            }

    # Le nom peut ne citer que la face avant d'une carte recto-verso.
    row = session.execute(text("""
        SELECT p.id, p.card_id, p.scryfall_id, p.set_code, p.collector_number
        FROM scryfall_card_printings p
        JOIN scryfall_cards c ON c.id = p.card_id
        WHERE (c.normalized_name = mm_normalize_name(:name)
               OR split_part(c.normalized_name, ' // ', 1) = mm_normalize_name(:name))
          AND p.lang = 'en'
          AND (:set IS NULL OR UPPER(p.set_code) = UPPER(:set))
        ORDER BY (c.normalized_name = mm_normalize_name(:name)) DESC,
                 (p.set_code NOT ILIKE 'sl%') DESC,
                 (p.image_normal IS NOT NULL) DESC, p.released_at DESC NULLS LAST
        LIMIT 1
    """), {"name": name, "set": set_code}).fetchone()
    if row:
        return {
            "printing_id": row.id, "card_id": row.card_id,
            "scryfall_id": row.scryfall_id,
            "set_code": row.set_code.upper(),
            "collector_number": row.collector_number,
        }

    # Carte connue mais sans impression anglaise exploitable : on garde la carte
    card_id = session.execute(text("""
        SELECT sc.id FROM scryfall_cards sc
        WHERE sc.normalized_name = mm_normalize_name(:name)
           OR split_part(sc.normalized_name, ' // ', 1) = mm_normalize_name(:name)
        ORDER BY (sc.normalized_name = mm_normalize_name(:name)) DESC,
                 (sc.type_line NOT ILIKE '%Token%') DESC, sc.id
        LIMIT 1
    """), {"name": name}).scalar()

    return {
        "printing_id": None, "card_id": card_id, "scryfall_id": None,
        "set_code": set_code.upper() if set_code else None,
        "collector_number": collector_number,
    }


def add_item(
    user_id: int,
    card_name: str,
    *,
    quantity: int = 1,
    set_code: str | None = None,
    collector_number: str | None = None,
    finish: str = "nonfoil",
    language: str = DEFAULT_LANGUAGE,
    condition: str | None = None,
    location: str | None = None,
    note: str | None = None,
    resolve: bool = True,
) -> dict:
    """Ajoute des exemplaires. Incremente la ligne si elle existe deja."""
    card_name = (card_name or "").strip()
    if not card_name:
        raise ValueError("Nom de carte manquant")
    quantity = max(1, int(quantity))
    finish = _clean_finish(finish)
    language = (language or DEFAULT_LANGUAGE).strip().lower()[:8]

    with SessionLocal() as session:
        scryfall_id = None
        card_id = None
        printing_id = None
        if resolve:
            found = _resolve_printing(session, card_name, set_code, collector_number)
            scryfall_id = found["scryfall_id"]
            card_id = found["card_id"]
            printing_id = found["printing_id"]
            set_code = found["set_code"]
            collector_number = found["collector_number"]

        row = session.execute(text("""
            INSERT INTO user_collection (
                user_id, card_name, quantity, set_code, collector_number,
                finish, language, condition, location, note, scryfall_id,
                card_id, printing_id, raw_line, added_at, updated_at
            ) VALUES (
                :uid, :name, :qty, :set, :num, :finish, :lang, :cond, :loc, :note, :sid,
                :card_id, :printing_id, :raw, NOW(), NOW()
            )
            ON CONFLICT (user_id, LOWER(TRIM(card_name)), COALESCE(set_code, ''),
                         COALESCE(collector_number, ''), finish, language,
                         COALESCE(condition, ''))
            DO UPDATE SET quantity   = user_collection.quantity + EXCLUDED.quantity,
                          updated_at = NOW(),
                          scryfall_id = COALESCE(user_collection.scryfall_id,
                                                 EXCLUDED.scryfall_id),
                          card_id     = COALESCE(user_collection.card_id,
                                                 EXCLUDED.card_id),
                          printing_id = COALESCE(user_collection.printing_id,
                                                 EXCLUDED.printing_id)
            RETURNING id, quantity
        """), {
            "uid": user_id, "name": card_name, "qty": quantity,
            "set": set_code, "num": collector_number, "finish": finish,
            "lang": language, "cond": condition, "loc": location, "note": note,
            "sid": scryfall_id, "card_id": card_id, "printing_id": printing_id,
            "raw": f"{quantity} {card_name}" + (f" ({set_code})" if set_code else ""),
        }).fetchone()
        session.commit()

    invalidate_stats(user_id)
    return {"id": row.id, "quantity": row.quantity, "card_name": card_name,
            "set_code": set_code, "finish": finish}


def set_item_printing(user_id: int, item_id: int, scryfall_id: str) -> dict | None:
    """Fixe l'edition possedee d'un exemplaire.

    L'affichage de la collection suit printing_id quand il est renseigne : c'est
    ce qui fait paraitre l'illustration et le code d'extension de l'edition que
    l'on possede reellement, et non d'une impression choisie par defaut.
    """
    with SessionLocal() as session:
        printing = session.execute(text("""
            SELECT id, card_id, UPPER(set_code) AS set_code, collector_number, scryfall_id
            FROM scryfall_card_printings WHERE scryfall_id = :sid
        """), {"sid": scryfall_id}).fetchone()
        if printing is None:
            return None

        row = session.execute(text("""
            UPDATE user_collection
               SET printing_id = :pid, card_id = COALESCE(card_id, :cid),
                   set_code = :set_code, collector_number = :num,
                   scryfall_id = :sid, updated_at = NOW()
             WHERE id = :id AND user_id = :uid
         RETURNING id, card_name, set_code
        """), {"pid": printing.id, "cid": printing.card_id,
               "set_code": printing.set_code, "num": printing.collector_number,
               "sid": printing.scryfall_id, "id": item_id, "uid": user_id}).fetchone()
        session.commit()

    if row is None:
        return None
    return {"id": row.id, "card_name": row.card_name, "set_code": row.set_code}


def update_item(user_id: int, item_id: int, **fields: Any) -> dict | None:
    """Met a jour un exemplaire. quantity <= 0 le supprime."""
    allowed = {"quantity", "condition", "location", "note", "finish", "language"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return None

    if "quantity" in updates:
        updates["quantity"] = int(updates["quantity"])
        if updates["quantity"] <= 0:
            delete_item(user_id, item_id)
            return {"deleted": True, "id": item_id}
    if "finish" in updates:
        updates["finish"] = _clean_finish(updates["finish"])

    assignments = ", ".join(f"{k} = :{k}" for k in updates)
    params = {**updates, "uid": user_id, "id": item_id}

    with SessionLocal() as session:
        row = session.execute(text(f"""
            UPDATE user_collection SET {assignments}, updated_at = NOW()
            WHERE id = :id AND user_id = :uid
            RETURNING id, card_name, quantity
        """), params).fetchone()
        session.commit()
    invalidate_stats(user_id)
    if row is None:
        return None
    return {"id": row.id, "card_name": row.card_name, "quantity": row.quantity}


def delete_item(user_id: int, item_id: int) -> bool:
    with SessionLocal() as session:
        row = session.execute(text(
            "DELETE FROM user_collection WHERE id = :id AND user_id = :uid RETURNING id"
        ), {"id": item_id, "uid": user_id}).fetchone()
        session.commit()
    invalidate_stats(user_id)
    return row is not None


def clear_collection(user_id: int) -> int:
    with SessionLocal() as session:
        count = session.execute(text(
            "DELETE FROM user_collection WHERE user_id = :uid"
        ), {"uid": user_id}).rowcount
        session.commit()
    invalidate_stats(user_id)
    return count or 0


def bulk_add(user_id: int, entries: list[dict]) -> dict:
    """Ajout en masse (import). Chaque entree : name, quantity, set_code, ..."""
    added = 0
    copies = 0
    errors: list[str] = []
    for entry in entries:
        name = (entry.get("name") or entry.get("card_name") or "").strip()
        if not name:
            continue
        try:
            add_item(
                user_id, name,
                quantity=entry.get("quantity", 1),
                set_code=entry.get("set_code"),
                collector_number=entry.get("collector_number"),
                finish=entry.get("finish") or "nonfoil",
                language=entry.get("language") or DEFAULT_LANGUAGE,
                condition=entry.get("condition"),
            )
            added += 1
            copies += max(1, int(entry.get("quantity", 1)))
        except Exception as exc:  # noqa: BLE001 — on continue l'import
            errors.append(f"{name}: {exc}")
    return {"added": added, "copies": copies, "errors": errors}


# ── Agregation par nom (pour les analyses) ────────────────────────────────────

def load_counts(user_id: int) -> dict[str, int]:
    """{ nom normalise: nombre total d'exemplaires } — vue attendue par les analyses."""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT LOWER(TRIM(card_name)) AS name, SUM(quantity) AS qty
            FROM user_collection WHERE user_id = :uid
            GROUP BY 1
        """), {"uid": user_id}).fetchall()
    return {r.name: int(r.qty) for r in rows}
