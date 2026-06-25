from __future__ import annotations

import csv
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMANDERS_FILE = ROOT / "data" / "My_commanders.txt"
FREQUENCY_CSV = ROOT / "data" / "stats" / "commander_frequency.csv"
SUMMARY_CSV = ROOT / "data" / "stats" / "commander_summary.csv"
MY_DECKS_DIR = ROOT / "data" / "My decks"


def _normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    return "".join(c for c in name if not unicodedata.combining(c)).lower().strip()


def _deck_contains_card(commander_name: str, card_norm: str) -> bool:
    """Retourne True si la carte est déjà présente dans le deck du commandant."""
    # Priorité Moxfield
    try:
        from manamind.moxfield_client import get_decklist_for_commander
        entries = get_decklist_for_commander(commander_name)
        if entries is not None:
            return any(_normalize(name) == card_norm for name, _ in entries)
    except Exception:
        pass

    # Fallback .txt local
    import re
    _re = re.compile(r"[^a-z0-9 ]")

    def _strip(s: str) -> str:
        return _re.sub(r" ", _normalize(s))

    cmd_clean = _strip(commander_name)
    if not MY_DECKS_DIR.exists():
        return False

    files = list(MY_DECKS_DIR.glob("*.txt"))
    deck_file = None
    words = [w for w in cmd_clean.split() if len(w) >= 4]
    for f in files:
        if cmd_clean.strip() in _strip(f.stem):
            deck_file = f
            break
    if deck_file is None and words:
        for f in files:
            if all(w in _strip(f.stem) for w in words):
                deck_file = f
                break
    if deck_file is None and words:
        for f in files:
            if words[0] in _strip(f.stem):
                deck_file = f
                break

    if deck_file is None:
        return False

    for line in deck_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        name = parts[1] if len(parts) == 2 and parts[0].isdigit() else line
        if _normalize(name) == card_norm:
            return True
    return False


def load_allowed_commanders() -> set[str]:
    if not COMMANDERS_FILE.exists():
        return set()
    lines = COMMANDERS_FILE.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip()}


def suggest_commanders(card_name: str, top_n: int = 3) -> list[dict]:
    """
    Retourne les `top_n` commandants qui jouent le plus souvent `card_name`,
    parmi ceux listés dans data/commanders.txt.

    Chaque résultat :
        {
            "commander": str,
            "inclusion_rate": float,   # pourcentage 0–100
            "decks_with_card": int,
            "total_decks": int,
        }
    """
    allowed = load_allowed_commanders()
    allowed_norm = {_normalize(c): c for c in allowed}

    card_norm = _normalize(card_name)

    # Chargement du summary pour le deck_count (départage à égalité)
    deck_count: dict[str, int] = {}
    if SUMMARY_CSV.exists():
        with open(SUMMARY_CSV, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cmd = row["commander"].strip()
                try:
                    deck_count[_normalize(cmd)] = int(row["deck_count"])
                except (ValueError, KeyError):
                    pass

    results: list[dict] = []

    if not FREQUENCY_CSV.exists():
        return results

    with open(FREQUENCY_CSV, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cmd_raw = row["commander"].strip()
            card_raw = row["card_name"].strip()

            if _normalize(card_raw) != card_norm:
                continue

            cmd_norm = _normalize(cmd_raw)
            if cmd_norm not in allowed_norm:
                continue

            try:
                inclusion_rate = float(row["inclusion_rate"])
                decks_with_card = int(row["decks_with_card"])
                total_decks = int(row["total_decks"])
            except (ValueError, KeyError):
                continue

            commander_display = allowed_norm[cmd_norm]
            if _deck_contains_card(commander_display, card_norm):
                continue

            results.append({
                "commander": commander_display,
                "inclusion_rate": round(inclusion_rate, 2),
                "decks_with_card": decks_with_card,
                "total_decks": total_decks,
            })

    # Tri : inclusion_rate DESC, puis nom du commandant ASC (départage égalité)
    results.sort(key=lambda r: (-r["inclusion_rate"], r["commander"]))
    return results[:top_n]


def _load_frequency_index() -> dict[str, dict[str, dict]]:
    """
    Charge commander_frequency.csv en mémoire.
    Retourne : { commander_norm: { card_norm: {inclusion_rate, decks_with_card, total_decks, card_name} } }
    """
    index: dict[str, dict[str, dict]] = {}
    if not FREQUENCY_CSV.exists():
        return index
    with open(FREQUENCY_CSV, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cmd_norm = _normalize(row["commander"].strip())
            card_raw = row["card_name"].strip()
            card_norm = _normalize(card_raw)
            try:
                ir = float(row["inclusion_rate"])
                dwc = int(row["decks_with_card"])
                td = int(row["total_decks"])
            except (ValueError, KeyError):
                continue
            if cmd_norm not in index:
                index[cmd_norm] = {}
            index[cmd_norm][card_norm] = {
                "card_name": card_raw,
                "inclusion_rate": ir,
                "decks_with_card": dwc,
                "total_decks": td,
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
    index = _load_frequency_index()

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
    index = _load_frequency_index()

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
