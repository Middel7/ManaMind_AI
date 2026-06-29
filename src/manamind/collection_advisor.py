from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COLLECTION_FILE = ROOT / "Ma collection.txt"
MY_DECKS_DIR    = ROOT / "data" / "My decks"
COMMANDERS_FILE = ROOT / "data" / "My_commanders.txt"
FREQUENCY_CSV   = ROOT / "data" / "stats" / "commander_frequency.csv"
OPENED_SETS_FILE = ROOT / "Opened.txt"
DECKLISTS_DIR   = ROOT / "data" / "Decklists"
TYPE_AVERAGES_CACHE = ROOT / "data" / "stats" / "deck_type_averages.json"

CARD_TYPES = ["Land", "Creature", "Instant", "Sorcery", "Enchantment", "Artifact", "Planeswalker"]


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    return "".join(c for c in name if not unicodedata.combining(c)).lower().strip()


def _cmd_norms(commander_name: str) -> set[str]:
    """Retourne l'ensemble des noms normalisés pour un commandant (gère Partner 'A + B')."""
    return {_normalize(n.strip()) for n in commander_name.split("+") if n.strip()}


def _cmd_freq(commander_name: str, freq_index: dict) -> dict:
    """Retourne les données EDHREC pour un commandant.
    Pour les Partner, essaie d'abord le nom complet 'A + B', puis chaque partie seule,
    et retourne les données du commandant avec le plus grand nombre de decks."""
    parts = [n.strip() for n in commander_name.split("+")]
    candidates: list[tuple[int, dict]] = []
    # Essai 1 : nom complet "A + B" (si indexé ainsi)
    full_norm = _normalize(commander_name)
    if full_norm in freq_index:
        data = freq_index[full_norm]
        sample = next(iter(data.values()), {})
        candidates.append((sample.get("total_decks", 0), data))
    # Essai 2 : chaque partie séparément
    for part in parts:
        norm = _normalize(part)
        if norm in freq_index:
            data = freq_index[norm]
            sample = next(iter(data.values()), {})
            candidates.append((sample.get("total_decks", 0), data))
    if not candidates:
        return {}
    # Retourner les données du commandant avec le plus de decks (meilleure base statistique)
    return max(candidates, key=lambda x: x[0])[1]


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
    """
    Retourne { norm: display } en fusionnant :
    1. Les commandants configurés dans Moxfield (priorité)
    2. Les commandants dans My_commanders.txt (fallback/complément)
    """
    result: dict[str, str] = {}

    # 1. Moxfield
    try:
        from manamind.moxfield_client import get_all_moxfield_commanders
        for name in get_all_moxfield_commanders():
            if name:
                result[_normalize(name)] = name
    except Exception:
        pass

    # 2. My_commanders.txt (seulement ceux non déjà couverts par Moxfield)
    if COMMANDERS_FILE.exists():
        for line in COMMANDERS_FILE.read_text(encoding="utf-8").splitlines():
            name = line.strip()
            if name and _normalize(name) not in result:
                result[_normalize(name)] = name

    return result


def load_opened_set_cards() -> dict[str, str]:
    """
    Retourne { card_norm: card_name } pour toutes les cartes C/UC
    appartenant aux sets listés dans Opened.txt, via la DB.
    """
    if not OPENED_SETS_FILE.exists():
        return {}
    set_codes = [
        l.strip().lower()
        for l in OPENED_SETS_FILE.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    if not set_codes:
        return {}
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "src"))
        from src.manamind.db.engine import SessionLocal as _SessionLocal
        from src.manamind.db.models.card import Card as _Card
        from src.manamind.db.models.card_printing import CardPrinting as _CardPrinting
        from sqlalchemy import select as _select
        with _SessionLocal() as session:
            stmt = (
                _select(_Card.name, _Card.normalized_name)
                .join(_CardPrinting, _CardPrinting.card_id == _Card.id)
                .where(
                    _CardPrinting.set_code.in_(set_codes),
                    _CardPrinting.rarity.in_(["common", "uncommon"]),
                    _CardPrinting.lang == "en",
                )
                .distinct()
            )
            rows = session.execute(stmt).all()
            return {_normalize(name): name for name, _ in rows}
    except Exception:
        return {}


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
    opened_set_cards = load_opened_set_cards()  # { norm: name } cartes C/UC des sets ouverts

    # Index des cartes par deck : { cmd_norm: set[card_norm] }
    deck_cards_index: dict[str, set[str]] = {}
    for cmd_norm, cmd_display in commanders.items():
        entries, source = _get_deck_entries(cmd_display)
        if not source:
            continue
        this_cmd_norms = _cmd_norms(cmd_display)
        deck_cards_index[cmd_norm] = {
            _normalize(name) for name, _ in entries
            if _normalize(name) not in this_cmd_norms
        }

    # Filtre optionnel sur un commandant unique
    if commander_filter:
        filter_norm = _normalize(commander_filter)
        commanders = {k: v for k, v in commanders.items() if k == filter_norm}

    # Cartes disponibles depuis la collection : quantité > nb decks où déjà utilisée
    available_collection: dict[str, tuple[int, str]] = {
        norm: (qty, "collection")
        for norm, qty in collection.items()
        if qty > deck_usage.get(norm, 0)
    }

    # Cartes C/UC des sets ouverts non déjà dans la collection disponible
    available_opened: dict[str, tuple[int, str]] = {
        norm: (0, "opened_sets")
        for norm, name in opened_set_cards.items()
        if norm not in available_collection and deck_usage.get(norm, 0) == 0
    }

    # Fusionner : collection prioritaire sur sets ouverts
    available: dict[str, tuple[int, str]] = {**available_opened, **available_collection}

    # Pour chaque carte disponible, trouver le meilleur commandant
    # (la carte ne doit pas déjà être dans le deck de ce commandant)
    best_per_card: dict[str, dict] = {}

    for cmd_norm, cmd_display in commanders.items():
        cmd_cards = _cmd_freq(cmd_display, freq_index)
        if not cmd_cards:
            continue
        this_deck = deck_cards_index.get(cmd_norm, set())
        for card_norm, (qty, source) in available.items():
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
                    "source":          source,
                }

    ranked = sorted(best_per_card.values(), key=lambda r: (-r["inclusion_rate"], r["card_name"]))

    return {
        "results": [{"rank": i + 1, **r} for i, r in enumerate(ranked[:top_n])],
        "stats": {
            "collection_size":   len(collection),
            "available_cards":   len(available_collection),
            "opened_sets_cards": len(available_opened),
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
    # Construire l'index : cmd_norm -> (cmd_display, set(card_norm))
    deck_cards: dict[str, set[str]] = {}
    valid_commanders: dict[str, str] = {}  # norm -> display, seulement ceux avec decklist
    for cmd_norm, cmd_display in commanders.items():
        entries, source = _get_deck_entries(cmd_display)
        if not source:
            continue
        valid_commanders[cmd_norm] = cmd_display
        deck_cards[cmd_norm] = {_normalize(name) for name, _ in entries}

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

    for from_norm, from_display in valid_commanders.items():
        from_freq  = _cmd_freq(from_display, freq_index)
        from_cmd_norms = _cmd_norms(from_display)
        entries, _ = _get_deck_entries(from_display)

        for card_name, _ in entries:
            card_norm = _normalize(card_name)

            if card_norm in from_cmd_norms or card_norm in land_norms:
                continue

            cards_scanned += 1
            from_data = from_freq.get(card_norm)
            from_rate = from_data["inclusion_rate"] if from_data else None

            if from_rate is None:
                continue

            # Chercher le meilleur autre commandant pour cette carte
            best_other_norm  = None
            best_other_disp  = None
            best_other_rate  = from_rate
            best_other_data  = None

            for to_norm, to_display in valid_commanders.items():
                if to_norm == from_norm:
                    continue
                if card_norm in deck_cards.get(to_norm, set()):
                    continue
                cmd_freq = _cmd_freq(to_display, freq_index)
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
            "decks_analyzed": len(valid_commanders),
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


def _get_deck_entries(commander_name: str) -> tuple[list[tuple[str, int]], str | None]:
    """
    Retourne ([(card_name, qty), ...], source) pour un commandant.
    Source = "moxfield" ou le nom du fichier .txt, ou None si introuvable.
    Priorité : Moxfield > fichier .txt local.
    """
    try:
        from manamind.moxfield_client import get_decklist_for_commander
        entries = get_decklist_for_commander(commander_name)
        if entries is not None:
            return entries, "moxfield"
    except Exception:
        pass

    deck_file = _find_deck_file(commander_name)
    if deck_file is None:
        return [], None

    entries = []
    for line in deck_file.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = _parse_card_line(line)
        if parsed:
            entries.append(parsed)
    return entries, deck_file.name


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
    entries, source = _get_deck_entries(commander_name)
    if not source:
        return {"commander": commander_name, "deck_file": None, "results": [], "unknown": [], "error": "Deck introuvable (ni Moxfield ni fichier local)"}

    freq_index = load_frequency_index()
    cmd_cards  = _cmd_freq(commander_name, freq_index)
    cmd_norms  = _cmd_norms(commander_name)

    deck_card_names: list[str] = [name for name, _ in entries]

    results: list[dict] = []
    unknown: list[str]  = []

    BASIC_LANDS = {"plains", "island", "swamp", "mountain", "forest",
                   "wastes", "snow-covered plains", "snow-covered island",
                   "snow-covered swamp", "snow-covered mountain", "snow-covered forest"}

    # Récupérer le total_decks pour ce commandant (pour les cartes absentes du CSV)
    total_decks_ref: int | None = None
    if cmd_cards:
        sample = next(iter(cmd_cards.values()))
        total_decks_ref = sample.get("total_decks")

    for name in deck_card_names:
        norm = _normalize(name)
        if norm in cmd_norms:
            continue  # sauter le(s) commandant(s)
        if norm in BASIC_LANDS:
            continue  # ne jamais suggérer de retirer un terrain de base
        if norm in cmd_cards:
            data = cmd_cards[norm]
            results.append({
                "card_name":      data["card_name"],
                "inclusion_rate": data["inclusion_rate"],
                "decks_with_card": data["decks_with_card"],
                "total_decks":    data["total_decks"],
            })
        else:
            # Carte absente du CSV = 0 deck similaire ne la joue → taux effectif de 0%
            results.append({
                "card_name":       name,
                "inclusion_rate":  0.0,
                "decks_with_card": 0,
                "total_decks":     total_decks_ref,
            })

    # Trier : inclusion_rate ASC (les moins jouées en premier)
    results.sort(key=lambda r: (r["inclusion_rate"] is None, r["inclusion_rate"] or 0, r["card_name"]))

    return {
        "commander":  commander_name,
        "deck_file":  source,
        "results":    [{"rank": i + 1, **r} for i, r in enumerate(results)],
        "unknown":    sorted(unknown),
        "error":      None,
    }


# ── Composition des decks par type ───────────────────────────────────────────

def _classify_card_type(type_line: str) -> str | None:
    """Retourne le premier type principal trouvé dans type_line, None si non classifiable."""
    tl = type_line or ""
    for t in CARD_TYPES:
        if t.lower() in tl.lower():
            return t
    return None


def _load_dfc_land_norms() -> set[str]:
    """
    Retourne tous les noms normalisés (complets et première partie) des cartes
    dont une face est un terrain : face sans mana_cost + type_line contenant 'Land',
    ou type_line de la carte contenant 'Land' sur l'une des parties (ex: 'Instant // Land').
    """
    result: set[str] = set()
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "src"))
        from manamind.db.engine import SessionLocal as _SessionLocal
        from manamind.db.models.card import Card as _Card
        from manamind.db.models.card_face import CardFace as _CardFace
        from sqlalchemy import select as _select, or_ as _or
        with _SessionLocal() as session:
            # Cartes avec une face-terrain (DFC classiques)
            stmt = (
                _select(_Card.normalized_name)
                .join(_CardFace, _CardFace.card_id == _Card.id)
                .where(
                    _CardFace.mana_cost.is_(None),
                    _CardFace.type_line.ilike("%Land%"),
                )
                .distinct()
            )
            for (norm,) in session.execute(stmt).all():
                result.add(norm)
                if "//" in norm:
                    result.add(norm.split("//")[0].strip())

            # MDFC dont le type_line global contient 'Land' sur l'une des parties
            # ex: 'Instant // Land', 'Sorcery // Land'
            stmt2 = (
                _select(_Card.normalized_name)
                .where(_Card.type_line.ilike("% // %land%"))
                .distinct()
            )
            for (norm,) in session.execute(stmt2).all():
                result.add(norm)
                if "//" in norm:
                    result.add(norm.split("//")[0].strip())
    except Exception:
        pass
    return result


def _load_card_type_map() -> dict[str, tuple[str, float | None]]:
    """
    Retourne { normalized_name: (type_principal, mana_value) } pour toutes les cartes de la DB.
    Pour les MDFC, indexe aussi par la première partie du nom.
    """
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "src"))
        from manamind.db.engine import SessionLocal as _SessionLocal
        from manamind.db.models.card import Card as _Card
        from sqlalchemy import select as _select
        with _SessionLocal() as session:
            stmt = _select(_Card.normalized_name, _Card.type_line, _Card.mana_value)
            rows = session.execute(stmt).all()
            result: dict[str, tuple[str, float | None]] = {}
            for norm, tl, mv in rows:
                if not norm or not tl:
                    continue
                t = _classify_card_type(tl)
                if t:
                    result[norm] = (t, mv)
                    if "//" in norm:
                        first = norm.split("//")[0].strip()
                        if first not in result:
                            result[first] = (t, mv)
            return result
    except Exception:
        return {}


def _count_types_in_deck(
    card_entries: list[tuple[str, int]],
    type_map: dict[str, tuple[str, float | None]],
    dfc_land_norms: set[str],
) -> tuple[dict[str, int], float | None]:
    """
    Compte le nombre de cartes par type et calcule le CMC moyen (hors terrains).
    Retourne (counts, avg_cmc).
    """
    counts: dict[str, int] = {t: 0 for t in CARD_TYPES}
    cmc_total = 0.0
    cmc_count = 0
    for name, qty in card_entries:
        norm = _normalize(name)
        if norm in dfc_land_norms:
            counts["Land"] += qty
            continue
        entry = type_map.get(norm)
        if entry:
            t, mv = entry
            counts[t] += qty
            if t != "Land" and mv is not None:
                cmc_total += mv * qty
                cmc_count += qty
    avg_cmc = round(cmc_total / cmc_count, 2) if cmc_count > 0 else None
    return counts, avg_cmc


def _compute_edhrec_averages(commander_name: str, type_map: dict[str, str], dfc_land_norms: set[str]) -> dict[str, float] | None:
    """
    Calcule les moyennes de composition par type pour un commandant
    à partir des CSVs dans data/Decklists/[commander]/.
    Retourne None si aucun decklist disponible.
    """
    cmd_dir = DECKLISTS_DIR / commander_name
    if not cmd_dir.exists():
        # Essai insensible à la casse + équivalence "&" / "+" pour les decks Partner
        try:
            name_lower = commander_name.lower()
            # Générer l'alternative avec l'autre séparateur Partner
            if " + " in name_lower:
                name_alt = name_lower.replace(" + ", " & ")
            else:
                name_alt = name_lower.replace(" & ", " + ")
            matches = [
                d for d in DECKLISTS_DIR.iterdir()
                if d.is_dir() and d.name.lower() in (name_lower, name_alt)
            ]
            if matches:
                cmd_dir = matches[0]
            else:
                return None
        except Exception:
            return None

    csv_files = list(cmd_dir.glob("*.csv"))
    if not csv_files:
        return None

    import csv as _csv
    totals: dict[str, float] = {t: 0.0 for t in CARD_TYPES}
    cmc_total = 0.0
    deck_count = 0

    for csv_file in csv_files:
        try:
            card_entries: list[tuple[str, int]] = []
            with open(csv_file, encoding="utf-8-sig", newline="") as f:
                reader = _csv.DictReader(f, delimiter=";")
                for row in reader:
                    name = (row.get("Card Name") or "").strip()
                    if name:
                        try:
                            qty = int(row.get("Quantity") or 1)
                        except (ValueError, TypeError):
                            qty = 1
                        card_entries.append((name, qty))
            if not card_entries:
                continue
            counts, avg_cmc = _count_types_in_deck(card_entries, type_map, dfc_land_norms)
            for t in CARD_TYPES:
                totals[t] += counts[t]
            if avg_cmc is not None:
                cmc_total += avg_cmc
            deck_count += 1
        except Exception:
            continue

    if deck_count == 0:
        return None

    result = {t: round(totals[t] / deck_count, 1) for t in CARD_TYPES}
    result["avg_cmc"] = round(cmc_total / deck_count, 2) if deck_count > 0 else None
    return result


def compute_deck_composition(commander_name: str) -> dict:
    """
    Retourne la composition par type du deck personnel et la moyenne EDHREC.

    {
        "commander": str,
        "my_deck": { "Land": int, "Creature": int, ... },
        "edhrec_avg": { "Land": float, "Creature": float, ... } | None,
        "edhrec_deck_count": int,
        "error": str | None,
    }
    """
    entries, source = _get_deck_entries(commander_name)
    if not source:
        return {"commander": commander_name, "my_deck": None, "edhrec_avg": None, "edhrec_deck_count": 0, "error": "Deck introuvable (ni Moxfield ni fichier local)"}

    # Charger les ressources DB
    type_map = _load_card_type_map()
    dfc_land_norms = _load_dfc_land_norms()

    # Mon deck — exclure le(s) commandant(s) (gère Partner)
    cmd_norms_set = _cmd_norms(commander_name)
    my_card_entries: list[tuple[str, int]] = [
        (name, qty) for name, qty in entries if _normalize(name) not in cmd_norms_set
    ]

    my_counts, my_avg_cmc = _count_types_in_deck(my_card_entries, type_map, dfc_land_norms)

    # EDHREC averages — depuis le cache ou recalcul
    cache_data: dict = {}
    if TYPE_AVERAGES_CACHE.exists():
        try:
            cache_data = json.loads(TYPE_AVERAGES_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache_data = {}

    cache_key = _normalize(commander_name)
    cached_entry = cache_data.get(cache_key)

    if cached_entry:
        edhrec_avg = cached_entry.get("avg")
        edhrec_deck_count = cached_entry.get("deck_count", 0)
    else:
        edhrec_avg = _compute_edhrec_averages(commander_name, type_map, dfc_land_norms)
        # Compter les decks pour l'affichage
        edhrec_deck_count = 0
        if edhrec_avg is not None:
            cmd_dir = DECKLISTS_DIR / commander_name
            if not cmd_dir.exists():
                try:
                    name_lower = commander_name.lower()
                    if " + " in name_lower:
                        name_alt = name_lower.replace(" + ", " & ")
                    else:
                        name_alt = name_lower.replace(" & ", " + ")
                    matches = [d for d in DECKLISTS_DIR.iterdir() if d.is_dir() and d.name.lower() in (name_lower, name_alt)]
                    if matches:
                        cmd_dir = matches[0]
                except Exception:
                    pass
            if cmd_dir.exists():
                edhrec_deck_count = len(list(cmd_dir.glob("*.csv")))

        if edhrec_avg is not None:
            cache_data[cache_key] = {"avg": edhrec_avg, "deck_count": edhrec_deck_count}
            try:
                TYPE_AVERAGES_CACHE.parent.mkdir(parents=True, exist_ok=True)
                TYPE_AVERAGES_CACHE.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

    return {
        "commander":         commander_name,
        "my_deck":           my_counts,
        "my_avg_cmc":        my_avg_cmc,
        "edhrec_avg":        edhrec_avg,
        "edhrec_avg_cmc":    edhrec_avg.get("avg_cmc") if edhrec_avg else None,
        "edhrec_deck_count": edhrec_deck_count,
        "error":             None,
    }
