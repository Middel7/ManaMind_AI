"""
decklist_strategy_analyzer.py
Analyse les decklists disponibles pour un commandant donné.
Calcule la fréquence des cartes, la distribution des types et des rôles,
et détecte des signaux stratégiques utiles à l'archetype_detector.
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = _ROOT / "data" / "recommendation_cache"

# Dossiers candidats pour les decklists, dans l'ordre de priorité
_DECKLIST_CANDIDATES = [
    _ROOT / "data" / "Decklists",
    _ROOT / "data" / "decklists",
    _ROOT / "data" / "All Decklists CSV",
    _ROOT / "data" / "All Decklists",
]

_MAX_FILES_WITHOUT_WARNING = 5000


def _normalize(name: str) -> str:
    """Normalise un nom de carte pour comparaison."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-zA-Z0-9\s]", "", name)
    return name.lower().strip()


def _find_commander_dir(commander_normalized: str) -> Path | None:
    """Cherche le sous-dossier de decklists correspondant au commandant."""
    for root in _DECKLIST_CANDIDATES:
        if not root.exists():
            continue
        try:
            for subdir in root.iterdir():
                if not subdir.is_dir():
                    continue
                norm_dir = _normalize(subdir.name)
                # Comparaison flexible : le slug du dossier contient celui du commandant
                if norm_dir == commander_normalized or commander_normalized in norm_dir or norm_dir in commander_normalized:
                    return subdir
                # Comparaison compacte (sans séparateurs)
                compact_dir = norm_dir.replace(" ", "").replace("_", "")
                compact_cmd = commander_normalized.replace("_", "").replace(" ", "")
                if compact_dir == compact_cmd or compact_cmd in compact_dir:
                    return subdir
        except PermissionError:
            continue
    return None


def _parse_csv_decklist(path: Path) -> set[str]:
    """Parse un fichier CSV de decklist et retourne un set de noms normalisés."""
    cards: set[str] = set()
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            content = f.read()
        delimiter = ";" if content.count(";") > content.count(",") else ","
        reader = csv.DictReader(content.splitlines(), delimiter=delimiter)
        for row in reader:
            name = ""
            for key in row:
                if "card" in key.lower() and "name" in key.lower():
                    name = (row[key] or "").strip()
                    break
            if name:
                cards.add(_normalize(name))
    except Exception:
        pass
    return cards


def _parse_txt_decklist(path: Path) -> set[str]:
    """Parse un fichier TXT de decklist."""
    cards: set[str] = set()
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("//"):
                    continue
                m = re.match(r"^\d+[xX]?\s+(.+)$", line)
                if m:
                    cards.add(_normalize(m.group(1)))
                else:
                    cards.add(_normalize(line))
    except Exception:
        pass
    return cards


def _get_cache_path(commander_normalized: str) -> Path:
    return _CACHE_DIR / f"commander_profile_decklist_analysis_{commander_normalized}.json"


def analyze_commander_decklists(
    commander_name: str,
    commander_normalized: str,
    card_role_inferer: Callable[[str], list[str]] | None = None,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    """
    Analyse les decklists disponibles pour un commandant.

    Args:
        commander_name: Nom affiché du commandant.
        commander_normalized: Slug normalisé (snake_case ASCII).
        card_role_inferer: Fonction optionnelle card_name -> [roles].
        force_rebuild: Ignorer le cache et refaire l'analyse.

    Returns:
        Dict avec fréquences, distribution, signaux stratégiques, confiance.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _get_cache_path(commander_normalized)

    # Charger le cache si disponible et non forcé
    if cache_path.exists() and not force_rebuild:
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            print(f"[Decklists] Cache analyse charge : {cache_path.name}")
            return cached
        except (json.JSONDecodeError, KeyError):
            pass

    empty_result = {
        "decklists_found": 0,
        "total_decklists_used": 0,
        "top_cards": [],
        "card_frequencies": {},
        "role_distribution": {},
        "card_type_distribution": {},
        "average_mana_curve": {},
        "overrepresented_cards": [],
        "decklist_signals": [],
        "confidence": 0.0,
    }

    commander_dir = _find_commander_dir(commander_normalized)
    if commander_dir is None:
        print(f"[Decklists] Aucun dossier trouve pour '{commander_name}'. Score decklist sera neutre.")
        return empty_result

    # Lister les fichiers
    csv_files = list(commander_dir.glob("*.csv"))
    txt_files = list(commander_dir.glob("*.txt"))
    all_files = csv_files + txt_files

    if not all_files:
        print(f"[Decklists] Dossier trouve mais vide : {commander_dir}")
        return empty_result

    if len(all_files) > _MAX_FILES_WITHOUT_WARNING:
        print(f"[Decklists] ATTENTION : {len(all_files)} fichiers dans {commander_dir.name}. Traitement limite.")
        all_files = all_files[:_MAX_FILES_WITHOUT_WARNING]

    print(f"[Decklists] Analyse de {len(all_files)} decklists dans {commander_dir.name}...")

    card_counts: dict[str, int] = defaultdict(int)
    total_decks = 0
    errors = 0

    for i, fpath in enumerate(all_files):
        if i > 0 and i % 500 == 0:
            print(f"[Decklists]   Progression : {i}/{len(all_files)}...")
        try:
            if fpath.suffix.lower() == ".csv":
                cards = _parse_csv_decklist(fpath)
            else:
                cards = _parse_txt_decklist(fpath)

            # Exclure le commandant lui-même
            cards.discard(commander_normalized)
            cards.discard(_normalize(commander_name))

            for card in cards:
                if card:
                    card_counts[card] += 1
            total_decks += 1
        except Exception:
            errors += 1

    if errors:
        print(f"[Decklists] {errors} fichiers ignores (erreur).")

    if total_decks == 0:
        print("[Decklists] Aucune decklist valide parsee.")
        return empty_result

    # Calculer les fréquences (taux d'apparition)
    card_frequencies = {
        card: round(count / total_decks, 4)
        for card, count in card_counts.items()
    }

    # Top 50 cartes
    top_cards = sorted(card_frequencies.items(), key=lambda x: x[1], reverse=True)[:50]

    # Cartes très surreprésentées (>70%)
    overrepresented = [c for c, f in top_cards if f >= 0.70]

    # Distribution des rôles (si le role_inferer est fourni)
    role_distribution: dict[str, float] = {}
    if card_role_inferer:
        role_totals: dict[str, float] = defaultdict(float)
        role_counts: dict[str, int] = defaultdict(int)
        # Analyser les 100 cartes les plus fréquentes
        for card_norm, freq in sorted(card_frequencies.items(), key=lambda x: x[1], reverse=True)[:100]:
            roles = card_role_inferer(card_norm)
            for role in roles:
                role_totals[role] += freq
                role_counts[role] += 1
        # Normaliser
        total_role_weight = sum(role_totals.values())
        if total_role_weight > 0:
            role_distribution = {
                role: round(w / total_role_weight, 4)
                for role, w in sorted(role_totals.items(), key=lambda x: x[1], reverse=True)
            }

    # Détecter les signaux stratégiques depuis les cartes les plus jouées
    decklist_signals: list[str] = []
    top_card_names = [c for c, _ in top_cards[:20]]

    # Heuristiques simples sur les noms de cartes pour détecter les stratégies
    signal_keywords = {
        "etb_synergy": ["panharmonicon", "conjurer", "ephemerate", "enter", "reflector"],
        "token_generation": ["anointed", "rhys", "parallel", "token", "spawning"],
        "blink": ["ephemerate", "restoration angel", "flickerwisp", "cloudshift"],
        "counter_strategy": ["hardened", "scales", "doubling season", "proliferate"],
        "graveyard_synergy": ["reanimate", "animate dead", "dredge", "flashback"],
        "sacrifice_synergy": ["altar", "ashnod", "phyrexian altar", "sacrifice"],
        "landfall": ["oracle of mul daya", "exploration", "burgeoning", "azusa"],
        "tribal": ["kindred", "lord of", "tribal", "changeling"],
        "spellslinger": ["arcane", "past in flames", "thousand year storm"],
        "artifact_synergy": ["myr", "workshop", "foundry", "tinker"],
        "cheat_creatures": ["sneak attack", "through the breach", "quicksilver amulet"],
        "attack_trigger": ["reconnaissance", "sword of", "goad", "dolmen gate"],
    }
    for signal, keywords in signal_keywords.items():
        if any(any(kw in card for kw in keywords) for card in top_card_names):
            decklist_signals.append(signal)

    # Score de confiance basé sur le nombre de decklists
    if total_decks >= 100:
        confidence = 0.9
    elif total_decks >= 30:
        confidence = 0.7
    elif total_decks >= 10:
        confidence = 0.5
    elif total_decks >= 3:
        confidence = 0.3
    else:
        confidence = 0.1

    result = {
        "decklists_found": len(all_files),
        "total_decklists_used": total_decks,
        "top_cards": [{"name": c, "frequency": f} for c, f in top_cards],
        "card_frequencies": dict(sorted(card_frequencies.items(), key=lambda x: x[1], reverse=True)[:500]),
        "role_distribution": role_distribution,
        "card_type_distribution": {},
        "average_mana_curve": {},
        "overrepresented_cards": overrepresented,
        "decklist_signals": decklist_signals,
        "confidence": confidence,
    }

    # Sauvegarder le cache
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"[Decklists] Analyse complete : {total_decks} decklists, {len(card_frequencies)} cartes. Cache sauvegarde.")

    return result
