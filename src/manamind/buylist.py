"""Ma buylist : les cartes à acheter pour compléter ses decks.

L'analyse parcourt chaque deck de l'utilisateur et retient les cartes les plus
jouées par son commandant qui manquent à ce deck, terrains exclus. Une carte
n'est à acheter que si l'utilisateur n'en a pas d'exemplaire libre : un
exemplaire possédé mais engagé dans un autre deck ne compte pas.

Une carte utile à plusieurs decks tient sur une seule ligne, qui nomme tous les
commandants concernés. La liste se classe par popularité : le taux d'inclusion
le plus haut de la carte parmi ces commandants.
"""

from __future__ import annotations

from sqlalchemy import text

from .collection_advisor import _cmd_freq_db, _normalize
from .db.engine import SessionLocal

TOP_DEFAULT = 100

# Suggestions retenues par deck avant le classement global : les premières
# du commandant, comme l'analyse d'un deck en propose une quarantaine.
SUGGESTIONS_PER_DECK = 40


def _key(name: str) -> str:
    """Clé d'une carte : nom normalisé, face avant seule."""
    return _normalize(name).split(" // ")[0].strip()


def _load_decks(session, user_id: int) -> list[dict]:
    rows = session.execute(text("""
        SELECT d.deck_id, COALESCE(d.name, d.commander) AS name, d.commander
        FROM user_moxfield_decks d
        WHERE d.user_id = :uid
          AND COALESCE(d.commander, '') NOT IN ('', 'Unknown')
        ORDER BY 2
    """), {"uid": user_id}).fetchall()
    return [{"deck_id": r.deck_id, "name": r.name, "commander": r.commander} for r in rows]


def _load_deck_cards(session, user_id: int) -> dict[str, list[tuple[str, int]]]:
    rows = session.execute(text("""
        SELECT dc.deck_id, dc.card_name, dc.quantity
        FROM user_deck_cards dc
        WHERE dc.user_id = :uid
          AND EXISTS (
              SELECT 1 FROM user_moxfield_decks d
              WHERE d.user_id = dc.user_id AND d.deck_id = dc.deck_id
          )
    """), {"uid": user_id}).fetchall()
    cards: dict[str, list[tuple[str, int]]] = {}
    for r in rows:
        cards.setdefault(r.deck_id, []).append((r.card_name, int(r.quantity or 1)))
    return cards


def _owned(session, user_id: int) -> dict[str, int]:
    rows = session.execute(text("""
        SELECT uc.card_name, SUM(uc.quantity) AS qty
        FROM user_collection uc
        WHERE uc.user_id = :uid
        GROUP BY 1
    """), {"uid": user_id}).fetchall()
    owned: dict[str, int] = {}
    for r in rows:
        owned[_key(r.card_name)] = owned.get(_key(r.card_name), 0) + int(r.qty or 0)
    return owned


def _lands(session, names: set[str]) -> set[str]:
    """Clés des cartes dont la face avant est un terrain.

    Une carte recto-verso sort / terrain (MDFC) reste un sort : seule la face
    avant compte. Les cartes d'art sont hors du projet.
    """
    if not names:
        return set()
    rows = session.execute(text("""
        WITH n AS (
            SELECT DISTINCT x AS raw, mm_normalize_name(x) AS nk
            FROM unnest(CAST(:names AS text[])) AS x
        )
        SELECT n.raw, c.type_line
        FROM n
        JOIN scryfall_cards c ON c.normalized_name = n.nk
        WHERE c.type_line NOT LIKE 'Card%'
    """), {"names": sorted(names)}).fetchall()
    found = {r.raw for r in rows}
    lands = {_key(r.raw) for r in rows
             if "Land" in (r.type_line or "").split("//")[0]}

    # Repli face avant pour les noms cités sans leur verso.
    missing = sorted(names - found)
    if missing:
        rows = session.execute(text("""
            WITH n AS (
                SELECT DISTINCT x AS raw, mm_normalize_name(x) AS nk
                FROM unnest(CAST(:names AS text[])) AS x
            )
            SELECT n.raw, c.type_line
            FROM n
            JOIN scryfall_cards c
              ON split_part(c.normalized_name, ' // ', 1) = n.nk
            WHERE c.type_line NOT LIKE 'Card%'
        """), {"names": missing}).fetchall()
        lands |= {_key(r.raw) for r in rows
                  if "Land" in (r.type_line or "").split("//")[0]}
    return lands


def _hidden_pairs(session, user_id: int) -> set[tuple[str, str]]:
    """Couples (deck, carte) que l'utilisateur a masqués."""
    rows = session.execute(text("""
        SELECT deck_id, card_key FROM user_buylist_hidden WHERE user_id = :uid
    """), {"uid": user_id}).fetchall()
    return {(r.deck_id, r.card_key) for r in rows}


def hide_card(user_id: int, card_name: str, deck_ids: list[str]) -> int:
    """Ne plus recommander `card_name` pour ces decks. Renvoie le nombre de
    decks concernés ; les decks qui ne sont pas à l'utilisateur sont ignorés."""
    if not deck_ids:
        return 0
    with SessionLocal() as session:
        owned = {r.deck_id for r in session.execute(text("""
            SELECT deck_id FROM user_moxfield_decks
            WHERE user_id = :uid AND deck_id = ANY(:ids)
        """), {"uid": user_id, "ids": list(deck_ids)})}
        for deck_id in owned:
            session.execute(text("""
                INSERT INTO user_buylist_hidden (user_id, deck_id, card_key, card_name)
                VALUES (:uid, :did, :key, :name)
                ON CONFLICT (user_id, deck_id, card_key) DO NOTHING
            """), {"uid": user_id, "did": deck_id, "key": _key(card_name),
                   "name": card_name})
        session.commit()
    return len(owned)


def list_hidden(user_id: int) -> list[dict]:
    """Cartes masquées, la plus récente d'abord, avec les decks concernés."""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT h.card_key, h.card_name, h.deck_id, h.hidden_at,
                   COALESCE(d.name, d.commander, h.deck_id) AS deck_name,
                   d.commander
            FROM user_buylist_hidden h
            LEFT JOIN user_moxfield_decks d
              ON d.user_id = h.user_id AND d.deck_id = h.deck_id
            WHERE h.user_id = :uid
            ORDER BY h.hidden_at DESC, deck_name
        """), {"uid": user_id}).fetchall()
    cards: dict[str, dict] = {}
    for r in rows:
        card = cards.setdefault(r.card_key, {"card_name": r.card_name, "decks": []})
        card["decks"].append({"deck_id": r.deck_id, "name": r.deck_name,
                              "commander": r.commander})
    return list(cards.values())


def restore_card(user_id: int, card_name: str) -> int:
    """Remet `card_name` dans les recommandations de tous les decks."""
    with SessionLocal() as session:
        result = session.execute(text("""
            DELETE FROM user_buylist_hidden WHERE user_id = :uid AND card_key = :key
        """), {"uid": user_id, "key": _key(card_name)})
        session.commit()
    return result.rowcount


def compute_buylist(user_id: int, top: int = TOP_DEFAULT) -> dict:
    """Les `top` cartes à acheter pour compléter les decks de l'utilisateur."""
    with SessionLocal() as session:
        decks = _load_decks(session, user_id)
        deck_cards = _load_deck_cards(session, user_id)
        owned = _owned(session, user_id)
        hidden = _hidden_pairs(session, user_id)

        # Exemplaires déjà engagés dans les decks, par carte.
        used: dict[str, int] = {}
        in_deck: dict[str, set[str]] = {}
        for deck in decks:
            keys = set()
            for name, qty in deck_cards.get(deck["deck_id"], []):
                k = _key(name)
                used[k] = used.get(k, 0) + qty
                keys.add(k)
            in_deck[deck["deck_id"]] = keys

        freq = {d["deck_id"]: _cmd_freq_db(d["commander"]) for d in decks}

        # Une entrée par carte : nom affiché, commandants concernés, popularité.
        lines: dict[str, dict] = {}

        def note(k: str, name: str, deck: dict) -> None:
            data = freq[deck["deck_id"]].get(_normalize(name)) \
                or freq[deck["deck_id"]].get(k) or {}
            rate = float(data.get("inclusion_rate") or 0)
            line = lines.setdefault(k, {"card_name": name, "decks": [], "popularity": 0.0})
            line["decks"].append({
                "deck_id": deck["deck_id"], "name": deck["name"],
                "commander": deck["commander"], "inclusion_rate": round(rate, 1),
            })
            line["popularity"] = max(line["popularity"], rate)

        for deck in decks:
            ranked = sorted(freq[deck["deck_id"]].values(),
                            key=lambda r: -(r["inclusion_rate"] or 0))
            taken = 0
            for row in ranked:
                if taken >= SUGGESTIONS_PER_DECK:
                    break
                k = _key(row["card_name"])
                # Une carte masquée cède sa place à la suivante du commandant.
                if k in in_deck[deck["deck_id"]] or (deck["deck_id"], k) in hidden:
                    continue
                note(k, row["card_name"], deck)
                taken += 1
        # Un exemplaire par deck qui la réclame, moins ceux restés libres.
        for k, line in lines.items():
            free = max(0, owned.get(k, 0) - used.get(k, 0))
            line["to_buy"] = len(line["decks"]) - free

        lands = _lands(session, {line["card_name"] for line in lines.values()})

    result = []
    for k, line in lines.items():
        if k in lands:
            continue
        to_buy = line["to_buy"]
        if to_buy <= 0:
            continue
        line["decks"].sort(key=lambda d: (-d["inclusion_rate"], d["name"]))
        result.append({
            "card_name": line["card_name"],
            "to_buy": to_buy,
            "owned": owned.get(k, 0),
            "popularity": round(line["popularity"], 1),
            "decks": line["decks"],
        })

    result.sort(key=lambda r: (-r["popularity"], r["card_name"]))
    return {
        "total": len(result),
        "cards": result[:top],
        "decks_analyzed": len(decks),
    }
