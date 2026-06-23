from __future__ import annotations

import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COLLECTION_FILE = ROOT / "Ma collection.txt"
MY_DECKS_DIR    = ROOT / "data" / "My decks"
COMMANDERS_FILE = ROOT / "data" / "My_commanders.txt"
FREQUENCY_CSV   = ROOT / "data" / "stats" / "commander_frequency.csv"


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    return "".join(c for c in name if not unicodedata.combining(c)).lower().strip()


def _parse_card_line(line: str) -> tuple[str, int] | None:
    """
    Parse une ligne au format Moxfield ou simple :
      "1 Sol Ring (CMD) #236 *F*"  → ("Sol Ring", 1)
      "2 Cultivate"                 → ("Cultivate", 2)
    Retourne None si la ligne est vide ou invalide.
    """
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("//"):
        return None
    # Retirer les annotations foil *F*, *E*, etc.
    line = re.sub(r'\s*\*[A-Z]\*\s*$', '', line)
    # Retirer "(SET) #num" ou "(SET) #nump"
    line = re.sub(r'\s*\([A-Z0-9]+\)\s*#\S+', '', line)
    line = line.strip()
    # Extraire la quantité en début de ligne
    m = re.match(r'^(\d+)[xX]?\s+(.+)$', line)
    if m:
        qty  = int(m.group(1))
        name = m.group(2).strip()
    else:
        qty  = 1
        name = line
    if not name:
        return None
    return name, qty


# ── Chargement des données ────────────────────────────────────────────────────

def load_collection() -> dict[str, int]:
    """
    Retourne { nom_carte: quantité_totale } depuis Ma collection.txt.
    """
    result: dict[str, int] = {}
    if not COLLECTION_FILE.exists():
        return result
    for line in COLLECTION_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = _parse_card_line(line)
        if parsed:
            name, qty = parsed
            norm = _normalize(name)
            result[norm] = result.get(norm, 0) + qty
    return result


def load_my_decks() -> dict[str, int]:
    """
    Retourne { nom_carte_normalisé: nb_decks_où_elle_est_utilisée }.
    Parcourt tous les .txt de data/My decks/.
    """
    usage: dict[str, set[str]] = {}
    if not MY_DECKS_DIR.exists():
        return {}
    for deck_file in MY_DECKS_DIR.glob("*.txt"):
        for line in deck_file.read_text(encoding="utf-8", errors="replace").splitlines():
            parsed = _parse_card_line(line)
            if parsed:
                name, _ = parsed
                norm = _normalize(name)
                if norm not in usage:
                    usage[norm] = set()
                usage[norm].add(deck_file.name)
    return {k: len(v) for k, v in usage.items()}


def load_allowed_commanders() -> dict[str, str]:
    """Retourne { norm: display } depuis My_commanders.txt."""
    if not COMMANDERS_FILE.exists():
        return {}
    lines = COMMANDERS_FILE.read_text(encoding="utf-8").splitlines()
    return {_normalize(l.strip()): l.strip() for l in lines if l.strip()}


def load_frequency_index() -> dict[str, dict[str, dict]]:
    """
    Charge commander_frequency.csv.
    Retourne { commander_norm: { card_norm: { card_name, inclusion_rate, decks_with_card, total_decks } } }
    """
    import csv
    index: dict[str, dict[str, dict]] = {}
    if not FREQUENCY_CSV.exists():
        return index
    with open(FREQUENCY_CSV, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cmd_norm  = _normalize(row["commander"].strip())
            card_raw  = row["card_name"].strip()
            card_norm = _normalize(card_raw)
            try:
                ir  = float(row["inclusion_rate"])
                dwc = int(row["decks_with_card"])
                td  = int(row["total_decks"])
            except (ValueError, KeyError):
                continue
            if cmd_norm not in index:
                index[cmd_norm] = {}
            index[cmd_norm][card_norm] = {
                "card_name":      card_raw,
                "inclusion_rate": ir,
                "decks_with_card": dwc,
                "total_decks":    td,
            }
    return index


# ── Algorithme principal ──────────────────────────────────────────────────────

def suggest_from_collection(top_n: int = 40, commander_filter: str | None = None) -> dict:
    """
    Retourne les `top_n` meilleures suggestions de cartes de la collection
    non encore utilisées dans tous les decks, avec leur meilleur commandant.

    Règle de disponibilité :
        quantité_en_collection > nb_decks_où_la_carte_est_déjà_utilisée

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
                    "copies_owned": int,
                    "copies_used": int,
                }
            ],
            "stats": {
                "collection_size": int,
                "available_cards": int,
                "commanders_checked": int,
            }
        }
    """
    collection  = load_collection()           # { norm: qty }
    deck_usage  = load_my_decks()             # { norm: nb_decks }
    commanders  = load_allowed_commanders()   # { norm: display }
    freq_index  = load_frequency_index()      # { cmd_norm: { card_norm: {...} } }

    # Index des cartes par deck : { cmd_norm: set[card_norm] }
    deck_cards_index: dict[str, set[str]] = {}
    for cmd_norm, cmd_display in commanders.items():
        f = _find_deck_file(cmd_display)
        if not f:
            continue
        cards: set[str] = set()
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            parsed = _parse_card_line(line)
            if parsed:
                cards.add(_normalize(parsed[0]))
        deck_cards_index[cmd_norm] = cards

    # Filtre optionnel sur un commandant unique
    if commander_filter:
        filter_norm = _normalize(commander_filter)
        commanders = {k: v for k, v in commanders.items() if k == filter_norm}

    # Cartes disponibles : quantité > nb decks où déjà utilisée
    available: dict[str, int] = {
        norm: qty
        for norm, qty in collection.items()
        if qty > deck_usage.get(norm, 0)
    }

    # Pour chaque carte disponible, trouver le meilleur commandant
    # (la carte ne doit pas déjà être dans le deck de ce commandant)
    best_per_card: dict[str, dict] = {}

    for cmd_norm, cmd_display in commanders.items():
        if cmd_norm not in freq_index:
            continue
        cmd_cards = freq_index[cmd_norm]
        this_deck = deck_cards_index.get(cmd_norm, set())
        for card_norm, qty in available.items():
            if card_norm not in cmd_cards:
                continue
            # Exclure si la carte est déjà dans ce deck
            if card_norm in this_deck:
                continue
            data = cmd_cards[card_norm]
            ir   = data["inclusion_rate"]
            existing = best_per_card.get(card_norm)
            if existing is None or ir > existing["inclusion_rate"]:
                best_per_card[card_norm] = {
                    "card_name":       data["card_name"],
                    "commander":       cmd_display,
                    "inclusion_rate":  ir,
                    "decks_with_card": data["decks_with_card"],
                    "total_decks":     data["total_decks"],
                    "copies_owned":    qty,
                    "copies_used":     deck_usage.get(card_norm, 0),
                }

    ranked = sorted(best_per_card.values(), key=lambda r: (-r["inclusion_rate"], r["card_name"]))

    return {
        "results": [{"rank": i + 1, **r} for i, r in enumerate(ranked[:top_n])],
        "stats": {
            "collection_size":   len(collection),
            "available_cards":   len(available),
            "commanders_checked": len(commanders),
        },
    }


# ── Déplacements inter-decks ──────────────────────────────────────────────────

def suggest_moves(top_n: int = 30) -> dict:
    """
    Trouve les cartes qui sont dans un de tes decks mais auraient un taux d'inclusion
    significativement meilleur dans un autre de tes decks (commandant reconnu).

    Critères :
      - La carte est dans le deck d'un commandant A (taux_A)
      - Elle a un taux taux_B dans le deck d'un commandant B, avec taux_B > taux_A
      - L'écart (taux_B - taux_A) classe les résultats : les plus grands écarts en premier

    Retourne :
        {
            "results": [
                {
                    "rank": int,
                    "card_name": str,
                    "from_commander": str,   # deck actuel
                    "from_rate": float,      # taux d'inclusion dans le deck actuel
                    "to_commander": str,     # deck cible
                    "to_rate": float,        # taux d'inclusion dans le deck cible
                    "gain": float,           # to_rate - from_rate
                    "decks_with_card": int,  # stats du deck cible
                    "total_decks": int,
                }
            ],
            "stats": {
                "decks_analyzed": int,
                "cards_scanned": int,
            }
        }
    """
    commanders  = load_allowed_commanders()   # {norm: display}
    freq_index  = load_frequency_index()      # {cmd_norm: {card_norm: {...}}}

    # Construire le mapping fichier -> (cmd_norm, cmd_display)
    deck_to_cmd: dict[str, tuple[str, str]] = {}
    for cmd_norm, cmd_display in commanders.items():
        f = _find_deck_file(cmd_display)
        if f:
            deck_to_cmd[f.name] = (cmd_norm, cmd_display)

    # Construire l'index inverse : cmd_norm -> set(card_norm) pour savoir
    # quelles cartes sont déjà dans chaque deck (évite les faux déplacements)
    deck_cards: dict[str, set[str]] = {}
    for deck_file in MY_DECKS_DIR.glob("*.txt"):
        if deck_file.name not in deck_to_cmd:
            continue
        cmd_norm_key, _ = deck_to_cmd[deck_file.name]
        cards_in_deck: set[str] = set()
        for line in deck_file.read_text(encoding="utf-8", errors="replace").splitlines():
            parsed = _parse_card_line(line)
            if parsed:
                cards_in_deck.add(_normalize(parsed[0]))
        deck_cards[cmd_norm_key] = cards_in_deck

    # Construire l'ensemble de tous les terrains (basiques + non-basiques) via la DB
    land_norms: set[str] = set()
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "src"))
        from src.manamind.db.engine import SessionLocal as _SessionLocal
        from src.manamind.db.models.card import Card as _Card
        from sqlalchemy import select as _select
        with _SessionLocal() as _session:
            _stmt = _select(_Card.normalized_name).where(_Card.type_line.ilike("%Land%"))
            land_norms = {row[0] for row in _session.execute(_stmt).all()}
    except Exception:
        pass
    # Fallback : terrains de base si la DB est indisponible
    if not land_norms:
        land_norms = {"plains", "island", "swamp", "mountain", "forest",
                      "wastes", "snow-covered plains", "snow-covered island",
                      "snow-covered swamp", "snow-covered mountain", "snow-covered forest"}

    # best_move[card_norm] = meilleur déplacement trouvé pour cette carte
    best_move: dict[str, dict] = {}
    cards_scanned = 0

    for deck_file in MY_DECKS_DIR.glob("*.txt"):
        if deck_file.name not in deck_to_cmd:
            continue
        from_norm, from_display = deck_to_cmd[deck_file.name]
        from_freq = freq_index.get(from_norm, {})

        for line in deck_file.read_text(encoding="utf-8", errors="replace").splitlines():
            parsed = _parse_card_line(line)
            if not parsed:
                continue
            card_name, _ = parsed
            card_norm = _normalize(card_name)

            if card_norm == from_norm or card_norm in land_norms:
                continue

            cards_scanned += 1
            from_data = from_freq.get(card_norm)
            from_rate = from_data["inclusion_rate"] if from_data else None

            # Ignorer les cartes dont le taux dans le deck source est inconnu :
            # on ne peut pas savoir si elles sont vraiment "mal placées".
            if from_rate is None:
                continue

            # Chercher le meilleur autre commandant pour cette carte
            best_other_norm  = None
            best_other_disp  = None
            best_other_rate  = from_rate
            best_other_data  = None

            for to_norm, to_display in commanders.items():
                if to_norm == from_norm:
                    continue
                # Bug fix : exclure les commandants qui ont déjà cette carte dans leur deck
                if card_norm in deck_cards.get(to_norm, set()):
                    continue
                cmd_freq = freq_index.get(to_norm, {})
                if card_norm not in cmd_freq:
                    continue
                other_data = cmd_freq[card_norm]
                other_rate = other_data["inclusion_rate"]
                if other_rate > best_other_rate:
                    best_other_rate  = other_rate
                    best_other_norm  = to_norm
                    best_other_disp  = to_display
                    best_other_data  = other_data

            if best_other_norm is None:
                continue  # pas de meilleur endroit disponible

            gain = best_other_rate - from_rate
            existing = best_move.get(card_norm)
            if existing is None or gain > existing["gain"]:
                best_move[card_norm] = {
                    "card_name":       from_data["card_name"],
                    "from_commander":  from_display,
                    "from_rate":       round(from_rate, 2),
                    "to_commander":    best_other_disp,
                    "to_rate":         round(best_other_rate, 2),
                    "gain":            round(gain, 2),
                    "decks_with_card": best_other_data["decks_with_card"],
                    "total_decks":     best_other_data["total_decks"],
                }

    # Commandants sans données dans le CSV (leurs cartes ont été ignorées)
    missing_data = sorted(
        cmd_display
        for cmd_norm, cmd_display in commanders.items()
        if cmd_norm not in freq_index
    )

    ranked = sorted(best_move.values(), key=lambda r: (-r["gain"], r["card_name"]))

    return {
        "results": [{"rank": i + 1, **r} for i, r in enumerate(ranked[:top_n])],
        "missing_data": missing_data,
        "stats": {
            "decks_analyzed": len(deck_to_cmd),
            "cards_scanned":  cards_scanned,
        },
    }


# ── Suggestions de retrait ────────────────────────────────────────────────────

def _find_deck_file(commander_name: str) -> Path | None:
    """
    Recherche le fichier .txt correspondant au commandant dans data/My decks/.
    Passe 1 : le nom normalisé est une sous-chaîne du stem normalisé.
    Passe 2 : chaque mot significatif (≥4 chars) du commandant est présent dans le stem.
    """
    if not MY_DECKS_DIR.exists():
        return None

    import re as _re

    def _strip(s: str) -> str:
        return _re.sub(r"[^a-z0-9 ]", " ", _normalize(s))

    cmd_clean = _strip(commander_name)

    files = list(MY_DECKS_DIR.glob("*.txt"))

    # Passe 1 : sous-chaîne directe
    for f in files:
        if cmd_clean.strip() in _strip(f.stem):
            return f

    # Passe 2 : tous les mots significatifs (≥ 4 chars) présents dans le stem
    words = [w for w in cmd_clean.split() if len(w) >= 4]
    if words:
        for f in files:
            stem_clean = _strip(f.stem)
            if all(w in stem_clean for w in words):
                return f

    # Passe 3 : au moins le premier mot long présent
    if words:
        for f in files:
            stem_clean = _strip(f.stem)
            if words[0] in stem_clean:
                return f

    return None


def suggest_cuts(commander_name: str) -> dict:
    """
    Pour un commandant donné, lit son decklist et retourne toutes ses cartes
    triées par inclusion_rate croissant (les moins populaires en premier).
    Les terrains de base sont inclus sans données de fréquence (inclusion_rate = None).

    Retourne :
        {
            "commander": str,
            "deck_file": str,
            "results": [
                {
                    "rank": int,
                    "card_name": str,
                    "inclusion_rate": float | None,
                    "decks_with_card": int | None,
                    "total_decks": int | None,
                }
            ],
            "unknown": [str],   # cartes absentes du CSV
            "error": str | None,
        }
    """
    deck_file = _find_deck_file(commander_name)
    if deck_file is None:
        return {"commander": commander_name, "deck_file": None, "results": [], "unknown": [], "error": "Fichier deck introuvable"}

    freq_index = load_frequency_index()
    cmd_norm   = _normalize(commander_name)
    cmd_cards  = freq_index.get(cmd_norm, {})

    # Charger toutes les cartes du deck
    deck_card_names: list[str] = []
    for line in deck_file.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = _parse_card_line(line)
        if parsed:
            name, _ = parsed
            deck_card_names.append(name)

    results: list[dict] = []
    unknown: list[str]  = []

    BASIC_LANDS = {"plains", "island", "swamp", "mountain", "forest",
                   "wastes", "snow-covered plains", "snow-covered island",
                   "snow-covered swamp", "snow-covered mountain", "snow-covered forest"}

    for name in deck_card_names:
        norm = _normalize(name)
        if norm == cmd_norm:
            continue  # sauter le commandant lui-même
        if norm in cmd_cards:
            data = cmd_cards[norm]
            results.append({
                "card_name":      data["card_name"],
                "inclusion_rate": data["inclusion_rate"],
                "decks_with_card": data["decks_with_card"],
                "total_decks":    data["total_decks"],
            })
        else:
            unknown.append(name)

    # Trier : inclusion_rate ASC (les moins jouées en premier), None à la fin
    results.sort(key=lambda r: (r["inclusion_rate"] is None, r["inclusion_rate"] or 0, r["card_name"]))

    return {
        "commander":  commander_name,
        "deck_file":  deck_file.name,
        "results":    [{"rank": i + 1, **r} for i, r in enumerate(results)],
        "unknown":    sorted(unknown),
        "error":      None,
    }
