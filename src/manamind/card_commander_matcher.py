from __future__ import annotations

import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# TODO: supprimer après migration — My_commanders.txt et My decks/ ne sont plus la source principale
COMMANDERS_FILE = ROOT / "data" / "My_commanders.txt"


def _normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    return "".join(c for c in name if not unicodedata.combining(c)).lower().strip()


def _deck_contains_card(commander_name: str, card_norm: str, user_id: int = 1) -> bool:
    """Retourne True si la carte est déjà présente dans le deck du commandant.

    Priorité : Moxfield (legacy JSON) > user_deck_cards (DB).
    """
    # 1. Priorité Moxfield (legacy)
    try:
        from manamind.moxfield_client import get_decklist_for_commander
        entries = get_decklist_for_commander(commander_name)
        if entries is not None:
            return any(_normalize(name) == card_norm for name, _ in entries)
    except Exception:
        pass

    # 2. DB user_deck_cards
    try:
        from sqlalchemy import text as _text
        from manamind.db.engine import SessionLocal as _SessionLocal
        with _SessionLocal() as s:
            row = s.execute(_text("""
                SELECT 1 FROM user_deck_cards
                WHERE user_id = :uid
                  AND LOWER(TRIM(commander)) = LOWER(TRIM(:cmd))
                  AND LOWER(TRIM(card_name)) = :card_norm
                LIMIT 1
            """), {"uid": user_id, "cmd": commander_name, "card_norm": card_norm}).fetchone()
        return row is not None
    except Exception:
        return False


def load_allowed_commanders(user_id: int = 1) -> set[str]:
    """Retourne l'ensemble des noms de commandants depuis user_moxfield_decks (DB).
    Fallback sur My_commanders.txt si la DB est vide ou indisponible.
    """
    result: set[str] = set()

    # 1. DB user_moxfield_decks
    try:
        from sqlalchemy import text as _text
        from manamind.db.engine import SessionLocal as _SessionLocal
        with _SessionLocal() as s:
            rows = s.execute(_text(
                "SELECT DISTINCT commander FROM user_moxfield_decks WHERE user_id = :uid AND commander IS NOT NULL"
            ), {"uid": user_id}).fetchall()
        result = {row.commander for row in rows if row.commander}
    except Exception:
        pass

    # 2. Fallback My_commanders.txt (TODO: supprimer après migration)
    if not result and COMMANDERS_FILE.exists():
        lines = COMMANDERS_FILE.read_text(encoding="utf-8").splitlines()
        result = {line.strip() for line in lines if line.strip()}

    return result


def suggest_commanders(card_name: str, top_n: int = 3) -> list[dict]:
    """
    Retourne les `top_n` commandants qui jouent le plus souvent `card_name`,
    parmi ceux listés dans data/commanders.txt.
    """
    from sqlalchemy import text as _text
    from manamind.db.engine import SessionLocal as _SessionLocal

    allowed = load_allowed_commanders()
    allowed_norm = {_normalize(c): c for c in allowed}
    card_norm = _normalize(card_name)

    results: list[dict] = []

    with _SessionLocal() as session:
        rows = session.execute(
            _text("""
                SELECT commander, inclusion_rate, decks_with_card, total_decks
                FROM deck_stat_commander
                WHERE card_name = :card
                ORDER BY inclusion_rate DESC
            """),
            {"card": card_name},
        ).fetchall()

    for row in rows:
        cmd_norm = _normalize(row.commander)
        if cmd_norm not in allowed_norm:
            continue
        commander_display = allowed_norm[cmd_norm]
        if _deck_contains_card(commander_display, card_norm):
            continue
        results.append({
            "commander":      commander_display,
            "inclusion_rate": round(row.inclusion_rate, 2),
            "decks_with_card": row.decks_with_card,
            "total_decks":    row.total_decks,
        })

    results.sort(key=lambda r: (-r["inclusion_rate"], r["commander"]))
    return results[:top_n]


def _load_commander_index_db(allowed_norm: dict[str, str]) -> dict[str, dict[str, dict]]:
    """
    Charge depuis PostgreSQL les stats pour tous les commandants autorisés.
    Retourne { commander_norm: { card_norm: {card_name, inclusion_rate, decks_with_card, total_decks} } }
    """
    from sqlalchemy import text as _text
    from manamind.db.engine import SessionLocal as _SessionLocal

    index: dict[str, dict[str, dict]] = {}
    if not allowed_norm:
        return index

    with _SessionLocal() as session:
        rows = session.execute(
            _text("""
                SELECT commander, card_name, inclusion_rate, decks_with_card, total_decks
                FROM deck_stat_commander
                WHERE commander = ANY(:cmds)
            """),
            {"cmds": list(allowed_norm.values())},
        ).fetchall()

    for row in rows:
        cmd_norm = _normalize(row.commander)
        card_norm = _normalize(row.card_name)
        if cmd_norm not in index:
            index[cmd_norm] = {}
        index[cmd_norm][card_norm] = {
            "card_name":       row.card_name,
            "inclusion_rate":  row.inclusion_rate,
            "decks_with_card": row.decks_with_card,
            "total_decks":     row.total_decks,
        }
    return index


def detect_commander(card_names: list[str]) -> dict | None:
    """
    Détecte le commandant le plus probable pour une liste de cartes.
    Calcule la moyenne des inclusion_rate de chaque carte connue
    pour chaque commandant autorisé, et retourne celui avec le score le plus élevé.

    Retourne :
        {
            "commander": str,
            "score": float,        # inclusion_rate moyen sur les cartes connues
            "matched_cards": int,  # nb de cartes de la liste trouvées dans le CSV
            "total_decks": int,
        }
    """
    allowed = load_allowed_commanders()
    allowed_norm = {_normalize(c): c for c in allowed}
    index = _load_commander_index_db(allowed_norm)

    input_norms = [_normalize(n) for n in card_names if n.strip()]

    scores: dict[str, dict] = {}
    for cmd_norm, cmd_display in allowed_norm.items():
        if cmd_norm not in index:
            continue
        cmd_cards = index[cmd_norm]
        matched = [cn for cn in input_norms if cn in cmd_cards]
        if not matched:
            continue
        avg_ir = sum(cmd_cards[cn]["inclusion_rate"] for cn in matched) / len(matched)
        total_decks = next(iter(cmd_cards.values()))["total_decks"]
        scores[cmd_norm] = {
            "commander": cmd_display,
            "score": round(avg_ir, 2),
            "matched_cards": len(matched),
            "total_decks": total_decks,
        }

    if not scores:
        return None

    best = max(scores.values(), key=lambda s: (s["score"], s["matched_cards"]))
    return best


def suggest_additions(card_names: list[str], top_n: int = 20) -> dict:
    """
    Pour chaque carte de la liste, cherche son taux d'inclusion dans chaque
    commandant autorisé (commanders.txt). Retourne les `top_n` meilleures
    combinaisons (carte, commandant) triées par inclusion_rate décroissant.

    Retourne :
        {
            "results": [
                {
                    "rank": int,
                    "card_name": str,
                    "commander": str,
                    "inclusion_rate": float,
                    "decks_with_card": int,
                    "total_decks": int,
                },
                ...
            ],
            "not_found": [str, ...]   # cartes absentes de tous les CSV
        }
    """
    allowed = load_allowed_commanders()
    allowed_norm = {_normalize(c): c for c in allowed}
    index = _load_commander_index_db(allowed_norm)

    input_norms = {_normalize(n): n for n in card_names if n.strip()}

    results: list[dict] = []
    not_found: set[str] = set(card_names)

    for cmd_norm, cmd_display in allowed_norm.items():
        if cmd_norm not in index:
            continue
        cmd_cards = index[cmd_norm]
        for card_norm, original_name in input_norms.items():
            if card_norm not in cmd_cards:
                continue
            data = cmd_cards[card_norm]
            not_found.discard(original_name)
            results.append({
                "card_name": data["card_name"],
                "commander": cmd_display,
                "inclusion_rate": data["inclusion_rate"],
                "decks_with_card": data["decks_with_card"],
                "total_decks": data["total_decks"],
            })

    # Déduplication : pour chaque carte, ne garder que le commandant avec le meilleur taux
    best_per_card: dict[str, dict] = {}
    for r in results:
        cn = r["card_name"]
        if cn not in best_per_card or r["inclusion_rate"] > best_per_card[cn]["inclusion_rate"]:
            best_per_card[cn] = r

    deduped = sorted(best_per_card.values(), key=lambda r: (-r["inclusion_rate"], r["card_name"]))

    return {
        "results": [{"rank": i + 1, **r} for i, r in enumerate(deduped[:top_n])],
        "not_found": sorted(not_found),
    }
