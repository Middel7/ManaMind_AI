"""Collections à thème : des objectifs de collection définis par des critères.

Une collection à thème (« Les anges », « Tout ce qui coûte 4 ») réunit les
éditions du catalogue qui répondent à ses critères. Elle est dynamique : ses
critères sont enregistrés, pas sa liste, et chaque ouverture les réévalue — une
extension qui sort avec de nouveaux anges les y fait entrer d'elle-même.

Une édition est une extension et un numéro, quelle que soit la langue : le
catalogue ne connaît pas toutes les impressions étrangères (aucune française
pour Innistrad Remastered, par exemple), et les éditions d'une collection en
français s'y réduisaient à celles qu'il connaissait. La langue de la
collection décide de ce qui est possédé : une édition l'est dès qu'un
exemplaire de cette langue la désigne, quelle que soit sa finition. Elle
choisit aussi l'illustration, quand le catalogue a une image dans cette langue. L'utilisateur peut aussi déclarer qu'il possède
toutes les éditions d'une carte : ce choix ne vaut que pour la collection où il
le fait, et ne touche pas à sa vraie collection.

Une collection peut aussi compter par illustration : les éditions d'une carte
qui reprennent la même image n'y font qu'une case, possédée dès que l'une
d'elles l'est. Une édition dont l'illustration est inconnue reste seule.

Les critères se combinent par « et » ; chacun peut être inversé (« sauf »).
Ils ne lisent que des colonnes présentes dans la base de production : le
catalogue du poste en a davantage (les finitions d'une impression, par
exemple), et une requête qui s'en servait y tombait en erreur.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import text

from .collection_store import printing_illustration_sql
from .db.engine import SessionLocal

# Au-delà, la page deviendrait illisible et la requête coûteuse : une
# collection aussi large demande un critère de plus.
MAX_EDITIONS = 6000

LANGUAGES = {"en", "fr", "de", "it", "es", "pt", "ja", "ko", "ru", "zhs", "zht"}

# Extensions jamais retenues par défaut dans le projet : illustrations hors
# charte (Secret Lair, Marvel, Tortues Ninja, Avatar Eternal).
_ALT_SETS = ("mar", "lmar", "pza", "tle")

_NUMERIC_OPS = {"eq": "=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}

_COLOR_LETTERS = {"W", "U", "B", "R", "G"}

# Cote d'une édition, finition indifférente : sa tendance, sinon sa tendance
# foil (les éditions qui n'existent qu'en foil), sinon la meilleure offre.
_PRICE_SQL = """COALESCE(NULLIF(x.trend_price, 0), NULLIF(x.foil_trend, 0),
                         NULLIF(x.low_price, 0), NULLIF(x.foil_low, 0))"""

# Champs offerts à la saisie, et la nature de leur valeur.
FIELDS = {
    "name": "text", "type": "text", "text": "text", "artist": "text",
    "keyword": "word", "function": "word", "frame": "word", "border": "word",
    "mana_value": "number", "power": "number", "toughness": "number",
    "year": "number", "price": "number",
    "colors": "colors", "identity": "colors",
    "rarity": "list", "set": "list",
    "full_art": "flag", "promo": "flag", "commander": "flag",
}


class CriteriaError(ValueError):
    """Critère illisible : le message est montré tel quel à l'utilisateur."""


# ── Critères → SQL ────────────────────────────────────────────────────────────

def _number(value) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise CriteriaError(f"« {value} » n'est pas un nombre.") from None


def _numeric_column(column: str) -> str:
    """Force et endurance sont du texte (« * », « 1+* ») : seules les valeurs
    chiffrées se comparent."""
    return f"(CASE WHEN {column} ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN {column}::numeric END)"


def _fragment(criterion: dict, key: str, params: dict) -> str:
    field = criterion.get("field")
    kind = FIELDS.get(field)
    if kind is None:
        raise CriteriaError(f"Critère inconnu : {field}.")
    value = criterion.get("value")
    op = criterion.get("op") or ""

    if kind == "text":
        word = str(value or "").strip()
        if not word:
            raise CriteriaError("Un critère de texte est vide.")
        params[key] = f"%{word}%"
        column = {"name": "c.name", "type": "c.type_line",
                  "text": "c.oracle_text", "artist": "p.artist"}[field]
        return f"{column} ILIKE :{key}"

    if kind == "word":
        word = str(value or "").strip()
        if not word:
            raise CriteriaError("Un critère est vide.")
        params[key] = word
        if field == "keyword":
            return (f"EXISTS (SELECT 1 FROM unnest(c.keywords) kw "
                    f"WHERE LOWER(kw) = LOWER(:{key}))")
        if field == "function":
            return (f"EXISTS (SELECT 1 FROM scryfall_card_tags tg "
                    f"WHERE tg.card_id = c.id AND tg.tag_name = :{key})")
        column = {"frame": "p.frame", "border": "p.border_color"}[field]
        return f"{column} = :{key}"

    if kind == "number":
        if op not in _NUMERIC_OPS:
            raise CriteriaError("Comparaison inconnue.")
        params[key] = _number(value)
        column = {
            "mana_value": "c.mana_value",
            "power": _numeric_column("c.power"),
            "toughness": _numeric_column("c.toughness"),
            "year": "EXTRACT(YEAR FROM p.released_at)",
            "price": _PRICE_SQL,
        }[field]
        return f"{column} {_NUMERIC_OPS[op]} :{key}"

    if kind == "colors":
        letters = {str(v).upper() for v in (value or [])}
        if not letters or not letters <= _COLOR_LETTERS | {"C"}:
            raise CriteriaError("Choisissez au moins une couleur.")
        column = "c.colors" if field == "colors" else "c.color_identity"
        if letters == {"C"}:
            return f"COALESCE(cardinality({column}), 0) = 0"
        letters.discard("C")
        params[key] = sorted(letters)
        array = f"CAST(:{key} AS varchar[])"
        if op == "include":
            return f"{column} @> {array}"
        if op == "within":
            return f"COALESCE({column}, '{{}}') <@ {array}"
        return f"({column} @> {array} AND COALESCE({column}, '{{}}') <@ {array})"

    if kind == "list":
        items = [str(v).strip().lower() for v in (value or []) if str(v).strip()]
        if not items:
            raise CriteriaError("Choisissez au moins une valeur.")
        params[key] = items
        column = "p.rarity" if field == "rarity" else "LOWER(p.set_code)"
        return f"{column} = ANY(:{key})"

    # kind == "flag"
    column = {"full_art": "p.full_art", "promo": "p.promo",
              "commander": "c.legal_commander"}[field]
    return f"{column} IS TRUE"


def build_where(criteria: list[dict]) -> tuple[str, dict]:
    """Traduit les critères en une condition SQL et ses paramètres.

    Les alias attendus sont `c` (scryfall_cards), `p` (l'impression) et `x`
    (sa dernière cote). Aucune valeur saisie n'entre dans le texte SQL : toutes
    passent en paramètres liés.
    """
    if not isinstance(criteria, list):
        raise CriteriaError("Critères illisibles.")
    if not criteria:
        raise CriteriaError("Ajoutez au moins un critère.")
    parts, params = [], {}
    for index, criterion in enumerate(criteria):
        if not isinstance(criterion, dict):
            raise CriteriaError("Critères illisibles.")
        fragment = _fragment(criterion, f"k{index}", params)
        # Une valeur absente (force d'un rituel) ne répond ni au critère ni à
        # son contraire : « sauf » garde donc les cartes sans valeur.
        parts.append(f"NOT COALESCE(({fragment}), FALSE)" if criterion.get("not")
                     else f"({fragment})")
    return " AND ".join(parts), params


def clean_definition(body: dict) -> dict:
    """Vérifie et normalise ce que la page envoie pour créer ou modifier."""
    name = str(body.get("name") or "").strip()
    if not name:
        raise CriteriaError("Donnez un nom à la collection.")
    language = str(body.get("language") or "en").strip().lower()
    if language not in LANGUAGES:
        raise CriteriaError("Langue inconnue.")
    criteria = body.get("criteria") or []
    build_where(criteria)
    return {"name": name[:120], "language": language, "criteria": criteria,
            "include_alt": bool(body.get("include_alt")),
            "by_illustration": bool(body.get("by_illustration"))}


# ── Évaluation ────────────────────────────────────────────────────────────────

def _editions(session, definition: dict) -> list[dict]:
    where, params = build_where(definition["criteria"])
    params = {**params, "lang": definition["language"], "cap": MAX_EDITIONS + 1}
    alt = "" if definition.get("include_alt") else f"""
          AND p.digital IS NOT TRUE
          AND p.set_code NOT ILIKE 'sl%%'
          AND LOWER(p.set_code) NOT IN {_ALT_SETS!r}"""
    rows = session.execute(text(f"""
        SELECT p.id AS printing_id, p.scryfall_id::text AS scryfall_id, p.cardmarket_id,
               c.id AS card_id, c.name, UPPER(p.set_code) AS set_code,
               p.collector_number, p.rarity, p.released_at,
               COALESCE(loc.image_small, p.image_small) AS image_small,
               COALESCE(loc.image_normal, p.image_normal) AS image_normal,
               st.name AS set_name, {_PRICE_SQL} AS price,
               {printing_illustration_sql(session)} AS illustration_id
        FROM scryfall_card_printings p
        JOIN scryfall_cards c ON c.id = p.card_id
        LEFT JOIN scryfall_mtg_sets st ON LOWER(st.code) = LOWER(p.set_code)
        LEFT JOIN LATERAL (
            SELECT pge.trend_price, pge.low_price, pge.foil_trend, pge.foil_low
            FROM cardmarket_price_guide_entries pge
            WHERE pge.id_product = p.cardmarket_id
            ORDER BY pge.captured_at DESC
            LIMIT 1
        ) x ON TRUE
        -- Illustration dans la langue de la collection, si le catalogue en a
        -- une vraie : une image « placeholder » n'est qu'un carton
        -- « Localized Image Not Available ».
        LEFT JOIN LATERAL (
            SELECT q.image_small, q.image_normal
            FROM scryfall_card_printings q
            WHERE :lang <> 'en' AND q.card_id = p.card_id AND q.lang = :lang
              AND q.set_code = p.set_code AND q.collector_number = p.collector_number
              AND q.image_status IN ('highres_scan', 'lowres')
              AND q.image_normal IS NOT NULL
            LIMIT 1
        ) loc ON TRUE
        WHERE p.lang = 'en'
          -- Cartes d'art et jetons ne sont pas des cartes à collectionner ici.
          AND c.type_line NOT LIKE 'Card%%'
          AND c.type_line NOT ILIKE 'Token%%'
          AND COALESCE(st.set_type, '') <> 'token'
          {alt}
          AND {where}
        ORDER BY c.name, p.released_at DESC NULLS LAST, p.collector_number
        LIMIT :cap
    """.replace("%%", "%")), params).mappings().all()
    if len(rows) > MAX_EDITIONS:
        raise CriteriaError(
            f"Plus de {MAX_EDITIONS:,} éditions répondent à ces critères : "
            "ajoutez-en un pour resserrer la collection.".replace(",", " "))
    return [dict(r) for r in rows]


def _owned_copies(session, user_id: int, language: str) -> dict:
    """Exemplaires possédés dans la langue de la collection, par (extension,
    numéro). L'impression enregistrée donne l'édition ; à défaut, l'extension
    et le numéro saisis."""
    return {(r.set_code, r.collector_number): r.copies for r in session.execute(text("""
        SELECT UPPER(COALESCE(p.set_code, uc.set_code)) AS set_code,
               COALESCE(p.collector_number, uc.collector_number) AS collector_number,
               SUM(uc.quantity) AS copies
        FROM user_collection uc
        LEFT JOIN scryfall_card_printings p ON p.id = uc.printing_id
        WHERE uc.user_id = :uid AND uc.language = :lang
        GROUP BY 1, 2
    """), {"uid": user_id, "lang": language}) if r.set_code and r.collector_number}


def _marked(session, collection_id: int) -> set[int]:
    return {r[0] for r in session.execute(text("""
        SELECT card_id FROM theme_collection_complete WHERE collection_id = :id
    """), {"id": collection_id})}


def _declared(session, collection_id: int) -> set[tuple[str, str]]:
    """Éditions déclarées possédées une à une, dans cette collection."""
    return {(r.set_code, r.collector_number) for r in session.execute(text("""
        SELECT set_code, collector_number FROM theme_collection_owned
        WHERE collection_id = :id
    """), {"id": collection_id})}


def _local_names(session, card_ids: list[int], language: str) -> dict[int, str]:
    """Nom imprimé de chaque carte dans la langue de la collection.

    Une collection en français se cherche sous « Faucheuse du Vol d'Argent de
    lune », pas seulement sous « Reaper of Flight Moonsilver ».
    """
    if language == "en" or not card_ids:
        return {}
    return {r.card_id: r.printed_name for r in session.execute(text("""
        SELECT DISTINCT ON (card_id) card_id, printed_name
        FROM scryfall_card_printings
        WHERE card_id = ANY(:ids) AND lang = :lang AND printed_name IS NOT NULL
        ORDER BY card_id, released_at DESC NULLS LAST
    """), {"ids": card_ids, "lang": language})}


def _num(value) -> float | None:
    return None if value is None else round(float(value), 2)


# Ce qu'une case garde de chacune de ses éditions : de quoi la nommer, la
# déclarer, et la montrer seule dans la fenêtre des boosters ouverts.
_MEMBER_KEYS = ("set_code", "set_name", "collector_number", "declared", "rarity",
                "released_at", "scryfall_id", "image_small", "image_normal")


def merge_by_illustration(editions: list[dict]) -> list[dict]:
    """Fond en une case les éditions d'une carte qui partagent une illustration.

    La case garde l'ordre de la première édition de son illustration. Elle se
    montre sous l'édition la plus ancienne, celle qui a fait l'illustration,
    mais prend le prix et le lien d'achat de la moins chère : c'est celle qu'on
    achèterait, et le coût des manquantes reste réaliste. Elle est possédée dès
    qu'une de ses éditions l'est, par un exemplaire ou une déclaration.
    ``editions`` liste toutes les éditions de l'illustration, avec celles qui
    sont déclarées : un « Annuler » doit toutes les viser. Sans
    ``illustration_id``, l'illustration est inconnue : l'édition reste seule.
    """
    groups: list[list[dict]] = []
    by_id: dict[str, list[dict]] = {}
    for edition in editions:
        key = edition.get("illustration_id")
        if key and key in by_id:
            by_id[key].append(edition)
            continue
        groups.append([edition])
        if key:
            by_id[key] = groups[-1]

    merged = []
    for group in groups:
        # Date ISO : l'ordre alphabétique est l'ordre chronologique. Sans date,
        # l'édition passe après les autres.
        oldest = min(group, key=lambda e: (e["released_at"] is None, e["released_at"] or ""))
        cheapest = min(group, key=lambda e: (e["price"] is None, e["price"] or 0))
        merged.append({
            **oldest,
            "price": cheapest["price"],
            "cardmarket_id": cheapest["cardmarket_id"],
            "buy_set_code": cheapest["set_code"],
            "buy_collector_number": cheapest["collector_number"],
            "copies": sum(e["copies"] for e in group),
            "declared": any(e["declared"] for e in group),
            "owned": any(e["owned"] for e in group),
            "editions": [{key: e[key] for key in _MEMBER_KEYS} for e in group],
        })
    return merged


def opened_wanted(cards: list[dict], opened: set[str]) -> list[dict]:
    """Éditions communes et peu communes des extensions ouvertes qui manquent.

    De quoi fouiller ses boîtes : une carte d'un booster ouvert a pu échapper
    au tri. Une édition y figure quand sa case n'est pas possédée — dans une
    collection comptée par illustration, posséder l'image dans une autre
    édition suffit à l'écarter. Le résultat est rangé par extension, la plus
    récente d'abord, et chaque carte par numéro.
    """
    opened = {code.upper() for code in opened}
    by_set: dict[str, dict] = {}
    for card in cards:
        for entry in card["editions"]:
            if entry["owned"]:
                continue
            for edition in entry.get("editions") or [entry]:
                if (edition["set_code"] not in opened
                        or edition["rarity"] not in ("common", "uncommon")):
                    continue
                group = by_set.setdefault(edition["set_code"], {
                    "set_code": edition["set_code"], "set_name": edition["set_name"],
                    "released_at": edition["released_at"], "cards": []})
                group["cards"].append({
                    "card_id": card["card_id"], "name": card["name"],
                    "local_name": card.get("local_name"),
                    **{key: edition[key] for key in ("collector_number", "rarity",
                                                     "scryfall_id", "image_small",
                                                     "image_normal")}})

    def number(card):
        digits = "".join(ch for ch in card["collector_number"] if ch.isdigit())
        return (int(digits) if digits else 0, card["collector_number"])

    for group in by_set.values():
        group["cards"].sort(key=number)
    return sorted(by_set.values(), key=lambda g: g["released_at"] or "", reverse=True)


def opened_sets(session, user_id: int) -> set[str]:
    """Extensions dont l'utilisateur a ouvert des boosters."""
    return {r[0].upper() for r in session.execute(text("""
        SELECT set_code FROM user_opened_sets WHERE user_id = :uid
    """), {"uid": user_id})}


def evaluate(session, user_id: int, definition: dict, collection_id: int | None = None) -> dict:
    """Les éditions de la collection, groupées par carte, avec leur possession.

    Une collection qui compte par illustration fond ensuite les éditions de
    chaque carte qui partagent une image (voir ``merge_by_illustration``).
    """
    editions = _editions(session, definition)
    owned_copies = _owned_copies(session, user_id, definition["language"])
    marked = _marked(session, collection_id) if collection_id else set()
    declared = _declared(session, collection_id) if collection_id else set()

    cards: dict[int, dict] = {}
    for e in editions:
        copies = owned_copies.get((e["set_code"], e["collector_number"]), 0)
        card = cards.setdefault(e["card_id"], {
            "card_id": e["card_id"], "name": e["name"],
            "marked": e["card_id"] in marked, "editions": []})
        card["editions"].append({
            "scryfall_id": e["scryfall_id"], "set_code": e["set_code"],
            "set_name": e["set_name"], "collector_number": e["collector_number"],
            "rarity": e["rarity"], "released_at": e["released_at"].isoformat()
            if e["released_at"] else None,
            "image_small": e["image_small"], "image_normal": e["image_normal"],
            "price": _num(e["price"]), "copies": copies,
            "cardmarket_id": e["cardmarket_id"],
            "declared": (e["set_code"], e["collector_number"]) in declared,
            "owned": bool(copies) or card["marked"]
                     or (e["set_code"], e["collector_number"]) in declared,
            "illustration_id": e["illustration_id"],
        })
    if definition.get("by_illustration"):
        for card in cards.values():
            card["editions"] = merge_by_illustration(card["editions"])
    local = _local_names(session, list(cards), definition["language"])
    for card in cards.values():
        card["local_name"] = local.get(card["card_id"])

    total = owned = 0
    value_owned = cost_missing = 0.0
    cover = None
    for card in cards.values():
        card["owned_count"] = sum(1 for e in card["editions"] if e["owned"])
        card["any_copy"] = any(e["copies"] or e["declared"] for e in card["editions"])
        for e in card["editions"]:
            total += 1
            if e["owned"]:
                owned += 1
                value_owned += e["price"] or 0
                if cover is None or (e["price"] or 0) > (cover["price"] or 0):
                    cover = e
            else:
                cost_missing += e["price"] or 0
    if cover is None and editions:
        cover = max((e for c in cards.values() for e in c["editions"]),
                    key=lambda e: e["price"] or 0)

    return {
        "cards": list(cards.values()),
        "stats": {
            "cards": len(cards), "editions": total, "owned": owned,
            "value_owned": round(value_owned, 2),
            "cost_missing": round(cost_missing, 2),
            "cover": {"image_normal": cover["image_normal"],
                      "image_small": cover["image_small"],
                      "scryfall_id": cover["scryfall_id"]} if cover else None,
        },
    }


# ── Lecture et écriture ───────────────────────────────────────────────────────

def _definition(row) -> dict:
    criteria = row.criteria
    if isinstance(criteria, str):
        criteria = json.loads(criteria)
    return {"id": row.id, "name": row.name, "language": row.language,
            "criteria": criteria, "include_alt": row.include_alt,
            "by_illustration": row.by_illustration}


def get(session, user_id: int, collection_id: int) -> dict | None:
    row = session.execute(text("""
        SELECT id, name, language, criteria, include_alt, by_illustration
        FROM theme_collections WHERE id = :id AND user_id = :uid
    """), {"id": collection_id, "uid": user_id}).fetchone()
    return _definition(row) if row else None


def list_collections(user_id: int) -> list[dict]:
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT id, name, language, criteria, include_alt, by_illustration
            FROM theme_collections WHERE user_id = :uid ORDER BY LOWER(name)
        """), {"uid": user_id}).fetchall()
        result = []
        for row in rows:
            definition = _definition(row)
            try:
                stats = evaluate(session, user_id, definition, row.id)["stats"]
                error = None
            except CriteriaError as exc:
                stats, error = None, str(exc)
            result.append({**definition, "stats": stats, "error": error})
        return result


def preview(user_id: int, definition: dict) -> dict:
    with SessionLocal() as session:
        data = evaluate(session, user_id, definition)
    sample = [card["editions"][0] | {"name": card["name"]} for card in data["cards"][:12]]
    return {"stats": data["stats"], "sample": sample}


def create(user_id: int, definition: dict) -> int:
    with SessionLocal() as session:
        new_id = session.execute(text("""
            INSERT INTO theme_collections (user_id, name, language, criteria, include_alt,
                                           by_illustration)
            VALUES (:uid, :name, :language, CAST(:criteria AS jsonb), :include_alt,
                    :by_illustration)
            RETURNING id
        """), {"uid": user_id, **definition,
               "criteria": json.dumps(definition["criteria"], ensure_ascii=False)}).scalar()
        session.commit()
        return new_id


def update(user_id: int, collection_id: int, definition: dict) -> bool:
    with SessionLocal() as session:
        done = session.execute(text("""
            UPDATE theme_collections
            SET name = :name, language = :language, criteria = CAST(:criteria AS jsonb),
                include_alt = :include_alt, by_illustration = :by_illustration,
                updated_at = NOW()
            WHERE id = :id AND user_id = :uid
        """), {"id": collection_id, "uid": user_id, **definition,
               "criteria": json.dumps(definition["criteria"], ensure_ascii=False)}).rowcount
        session.commit()
        return bool(done)


def delete(user_id: int, collection_id: int) -> bool:
    with SessionLocal() as session:
        done = session.execute(text("""
            DELETE FROM theme_collections WHERE id = :id AND user_id = :uid
        """), {"id": collection_id, "uid": user_id}).rowcount
        session.commit()
        return bool(done)


def duplicate(user_id: int, collection_id: int) -> int | None:
    with SessionLocal() as session:
        source = get(session, user_id, collection_id)
    if source is None:
        return None
    copy_name = re.sub(r"\s*\(copie\)$", "", source["name"]) + " (copie)"
    return create(user_id, {"name": copy_name, "language": source["language"],
                            "criteria": source["criteria"],
                            "include_alt": source["include_alt"],
                            "by_illustration": source["by_illustration"]})


def set_complete(user_id: int, collection_id: int, card_id: int, complete: bool) -> bool:
    """Déclare, ou non, toutes les éditions d'une carte possédées dans cette
    collection seulement."""
    with SessionLocal() as session:
        if get(session, user_id, collection_id) is None:
            return False
        if complete:
            session.execute(text("""
                INSERT INTO theme_collection_complete (collection_id, card_id)
                VALUES (:id, :card) ON CONFLICT DO NOTHING
            """), {"id": collection_id, "card": card_id})
        else:
            session.execute(text("""
                DELETE FROM theme_collection_complete
                WHERE collection_id = :id AND card_id = :card
            """), {"id": collection_id, "card": card_id})
        session.commit()
        return True


def set_edition_owned(user_id: int, collection_id: int, set_code: str,
                      collector_number: str, owned: bool) -> bool:
    """Déclare, ou non, une édition possédée dans cette collection seulement."""
    params = {"id": collection_id, "set": set_code.strip().upper(),
              "num": collector_number.strip()}
    with SessionLocal() as session:
        if get(session, user_id, collection_id) is None:
            return False
        if owned:
            session.execute(text("""
                INSERT INTO theme_collection_owned (collection_id, set_code, collector_number)
                VALUES (:id, :set, :num) ON CONFLICT DO NOTHING
            """), params)
        else:
            session.execute(text("""
                DELETE FROM theme_collection_owned
                WHERE collection_id = :id AND set_code = :set AND collector_number = :num
            """), params)
        session.commit()
        return True
