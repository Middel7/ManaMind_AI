#!/usr/bin/env python3
"""
Recommandations vectorielles V2 — Moteur hybride MTG Commander
===============================================================
Moteur hybride combinant :
  - Filtrage strict Commander (couleur + légalité)
  - Profil stratégique du commandant
  - Détection des rôles des cartes
  - Score de popularité dans les decklists
  - Score vectoriel (embeddings BAAI/bge-m3)
  - Score de synergie commandant
  - Score EDHREC
  - Score courbe de mana
  - Score qualité
  - Score final explicable avec détail par composante

Usage :
    python "scripts/Recommandations vectorielles V2.py" --commander "Galadriel, Light of Valinor"
    python "scripts/Recommandations vectorielles V2.py" --commander "Galadriel, Light of Valinor" --query "plus de pioche et ramp"
    python "scripts/Recommandations vectorielles V2.py" --commander "Galadriel, Light of Valinor" --deck-file uploads/my_deck.txt --top 30
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# ── PYTHONPATH ─────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

# ── Chemins ────────────────────────────────────────────────────────────────────
EMBEDDINGS_FILE = _ROOT / "data" / "embeddings" / "card_embeddings.npy"
METADATA_FILE = _ROOT / "data" / "embeddings" / "card_metadata.json"
CACHE_DIR = _ROOT / "data" / "recommendation_cache"
DECKLISTS_ROOT = _ROOT / "data" / "Decklists"
OUTPUT_DIR = _ROOT / "outputs" / "recommendations"

# ── Module de profil commandant (nouveau) ──────────────────────────────────────
from src.recommendations.commander_profile.profile_builder import (  # noqa: E402
    load_or_create_commander_profile,
)
from src.recommendations.commander_profile.profile_cache import (  # noqa: E402
    normalize_slug as _normalize_filename,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Environnement & base de données
# ══════════════════════════════════════════════════════════════════════════════

def load_env() -> str:
    """Charge DATABASE_URL depuis .env ou l'environnement."""
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("[ERREUR] DATABASE_URL absent. Crée un fichier .env avec DATABASE_URL=postgresql://...")
        sys.exit(1)
    return url


def get_database_engine(url: str) -> Any:
    """Crée et vérifie la connexion SQLAlchemy."""
    from sqlalchemy import create_engine, text
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("[DB] Connexion à la base de données OK.")
        return engine
    except Exception as exc:
        print(f"[ERREUR] Impossible de se connecter à la base : {exc}")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Normalisation
# ══════════════════════════════════════════════════════════════════════════════

def normalize_card_name(name: str) -> str:
    """Normalise un nom de carte : minuscules, sans accents, sans ponctuation."""
    if not name:
        return ""
    name = name.strip()
    # Supprimer le préfixe Alchemy
    if name.startswith("A-"):
        name = name[2:]
    # Normalisation unicode → ASCII
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    return name.lower().strip()


# ══════════════════════════════════════════════════════════════════════════════
# 3. Chargement du commandant
# ══════════════════════════════════════════════════════════════════════════════

def load_commander(engine: Any, commander_name: str) -> dict[str, Any]:
    """
    Cherche le commandant dans la table cards.
    Essaie : égalité sur name, puis normalized_name, puis ILIKE.
    """
    from sqlalchemy import text

    queries = [
        ("name exact", "SELECT id, oracle_id, name, normalized_name, mana_cost, mana_value, type_line, oracle_text, colors, color_identity, keywords, legal_commander, edhrec_rank FROM cards WHERE name = :n LIMIT 1"),
        ("normalized_name exact", "SELECT id, oracle_id, name, normalized_name, mana_cost, mana_value, type_line, oracle_text, colors, color_identity, keywords, legal_commander, edhrec_rank FROM cards WHERE normalized_name = :n LIMIT 1"),
        ("ILIKE", "SELECT id, oracle_id, name, normalized_name, mana_cost, mana_value, type_line, oracle_text, colors, color_identity, keywords, legal_commander, edhrec_rank FROM cards WHERE name ILIKE :n LIMIT 1"),
    ]
    norm = normalize_card_name(commander_name)

    with engine.connect() as conn:
        for label, sql in queries:
            param = commander_name if "exact" in label and "normalized" not in label else (norm if "normalized" in label else f"%{commander_name}%")
            row = conn.execute(text(sql), {"n": param}).fetchone()
            if row:
                card = dict(row._mapping)
                print(f"[Commandant] Trouvé via {label} : {card['name']}")
                print(f"[Commandant] Identité couleur : {card['color_identity']}")
                return card

        # Pas trouvé → suggestions
        like_sql = "SELECT name FROM cards WHERE name ILIKE :n OR normalized_name ILIKE :n2 LIMIT 5"
        parts = commander_name.split()
        pattern = f"%{parts[0]}%"
        rows = conn.execute(text(like_sql), {"n": pattern, "n2": pattern}).fetchall()
        if rows:
            suggestions = [r[0] for r in rows]
            print(f"[ERREUR] Commandant '{commander_name}' introuvable.")
            print(f"         Suggestions : {', '.join(suggestions)}")
        else:
            print(f"[ERREUR] Commandant '{commander_name}' introuvable et aucune suggestion.")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Filtrage Commander
# ══════════════════════════════════════════════════════════════════════════════

def is_color_identity_legal(card_colors: list[str] | None, commander_colors: list[str] | None) -> bool:
    """Vérifie que l'identité couleur de la carte est un sous-ensemble de celle du commandant."""
    card_set = set(card_colors or [])
    cmd_set = set(commander_colors or [])
    return card_set.issubset(cmd_set)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Chargement des cartes candidates depuis la DB
# ══════════════════════════════════════════════════════════════════════════════

def load_cards_from_database(engine: Any, commander: dict[str, Any], current_deck_names: set[str]) -> list[dict[str, Any]]:
    """
    Charge toutes les cartes legal_commander=true depuis la DB.
    Applique le filtre couleur strict avant de retourner.
    """
    from sqlalchemy import text

    sql = """
        SELECT id, oracle_id, name, normalized_name, mana_cost, mana_value,
               type_line, oracle_text, colors, color_identity, keywords,
               legal_commander, edhrec_rank
        FROM cards
        WHERE legal_commander = true
        ORDER BY edhrec_rank ASC NULLS LAST
    """
    cmd_colors = commander.get("color_identity") or []
    cmd_id = commander["id"]
    cmd_oracle_id = commander.get("oracle_id")

    with engine.connect() as conn:
        rows = conn.execute(text(sql)).fetchall()

    total = len(rows)
    excluded_color = 0
    excluded_deck = 0
    excluded_self = 0
    candidates = []

    for row in rows:
        card = dict(row._mapping)

        # Exclure le commandant lui-même
        if card["id"] == cmd_id or (cmd_oracle_id and card.get("oracle_id") == cmd_oracle_id):
            excluded_self += 1
            continue

        # Filtre couleur strict
        if not is_color_identity_legal(card.get("color_identity"), cmd_colors):
            excluded_color += 1
            continue

        # Exclure cartes déjà dans le deck
        norm = normalize_card_name(card["name"])
        if norm in current_deck_names:
            excluded_deck += 1
            continue

        candidates.append(card)

    print(f"[Cartes] Total DB (legal_commander) : {total}")
    print(f"[Cartes] Exclues (commandant lui-même) : {excluded_self}")
    print(f"[Cartes] Exclues (hors identité couleur) : {excluded_color}")
    print(f"[Cartes] Exclues (déjà dans le deck) : {excluded_deck}")
    print(f"[Cartes] Candidates après filtrage : {len(candidates)}")
    return candidates


# ══════════════════════════════════════════════════════════════════════════════
# 6. Profil stratégique du commandant
# ══════════════════════════════════════════════════════════════════════════════
# load_or_create_commander_profile est importé depuis
# src/recommendations/commander_profile/profile_builder.py


# ══════════════════════════════════════════════════════════════════════════════
# 7. Détection des rôles des cartes
# ══════════════════════════════════════════════════════════════════════════════

def infer_card_roles(card: dict[str, Any]) -> list[str]:
    """Détecte les rôles stratégiques d'une carte à partir de son texte et type."""
    text = (card.get("oracle_text") or "").lower()
    type_line = (card.get("type_line") or "").lower()
    keywords = [k.lower() for k in (card.get("keywords") or [])]
    mv = card.get("mana_value") or 0

    roles: list[str] = []

    # ── Ramp ──────────────────────────────────────────────────────────────────
    if ("search your library" in text and "land" in text) or \
       ("add" in text and any(c in text for c in ["{g}", "{w}", "{u}", "{b}", "{r}", "mana of any"])):
        roles.append("ramp")
    if "artifact" in type_line and "add" in text:
        roles.append("ramp")

    # ── Card draw ─────────────────────────────────────────────────────────────
    if "draw a card" in text or "draw two cards" in text or "draw cards" in text or \
       "draw x cards" in text or "draw that many" in text:
        roles.append("card_draw")

    # ── ETB synergy ───────────────────────────────────────────────────────────
    if "enters the battlefield" in text or "enters, " in text:
        roles.append("etb_synergy")

    # ── Token generation ──────────────────────────────────────────────────────
    if "create" in text and ("token" in text or "tokens" in text):
        roles.append("token_generation")

    # ── Counter synergy ───────────────────────────────────────────────────────
    if "+1/+1 counter" in text or "+1/+1 counters" in text:
        roles.append("counter_synergy")

    # ── Targeted removal ──────────────────────────────────────────────────────
    if ("destroy target" in text or "exile target" in text) and \
       any(w in text for w in ["creature", "permanent", "artifact", "enchantment", "planeswalker"]):
        roles.append("targeted_removal")

    # ── Board wipe ────────────────────────────────────────────────────────────
    if ("destroy all" in text or "exile all" in text or "deals" in text and "damage to each" in text) and \
       any(w in text for w in ["creatures", "permanents", "nonland"]):
        roles.append("boardwipe")

    # ── Counterspell ──────────────────────────────────────────────────────────
    if "counter target spell" in text or "counter that spell" in text or \
       "counter target" in text:
        roles.append("counterspell")

    # ── Protection ────────────────────────────────────────────────────────────
    if any(k in keywords for k in ["hexproof", "shroud", "indestructible"]) or \
       "hexproof" in text or "indestructible" in text or "phase out" in text or \
       "protection from" in text:
        roles.append("protection")

    # ── Blink ─────────────────────────────────────────────────────────────────
    if ("exile" in text and "return it to the battlefield" in text) or \
       ("return target" in text and "hand" in text and "you control" in text):
        roles.append("blink")

    # ── Anthem ────────────────────────────────────────────────────────────────
    if ("creatures you control get" in text or "each creature you control gets" in text) and \
       ("+" in text):
        roles.append("anthem")

    # ── Tribal ────────────────────────────────────────────────────────────────
    tribal_types = ["elf", "goblin", "human", "wizard", "knight", "soldier",
                    "dragon", "vampire", "zombie", "merfolk", "angel"]
    if any(t in text or t in type_line for t in tribal_types):
        roles.append("tribal_synergy")

    # ── Graveyard synergy ─────────────────────────────────────────────────────
    if "graveyard" in text and ("return" in text or "exile" in text or "mill" in text):
        roles.append("graveyard_synergy")

    # ── Sacrifice synergy ─────────────────────────────────────────────────────
    if "sacrifice" in text and ("as a cost" in text or "whenever you sacrifice" in text or
                                  "sacrifice a" in text):
        roles.append("sacrifice_synergy")

    # ── Lifegain ──────────────────────────────────────────────────────────────
    if "gain" in text and ("life" in text):
        roles.append("lifegain")

    # ── Landfall ──────────────────────────────────────────────────────────────
    if "landfall" in text or ("land enters the battlefield under your control" in text):
        roles.append("landfall")

    # ── Artifact synergy ──────────────────────────────────────────────────────
    if "artifact" in type_line or ("artifact" in text and ("you control" in text or "whenever" in text)):
        roles.append("artifact_synergy")

    # ── Enchantment synergy ───────────────────────────────────────────────────
    if "enchantment" in type_line or ("enchantment" in text and ("you control" in text or "whenever" in text)):
        roles.append("enchantment_synergy")

    # ── Tutor ─────────────────────────────────────────────────────────────────
    if "search your library" in text and ("put" in text or "reveal" in text):
        roles.append("tutor")

    # ── Recursion ─────────────────────────────────────────────────────────────
    if "return" in text and "graveyard" in text and ("to your hand" in text or "to the battlefield" in text):
        roles.append("recursion")

    # ── Stax ──────────────────────────────────────────────────────────────────
    if ("opponents can't" in text or "each opponent" in text and "pay" in text) or \
       "tax" in text:
        roles.append("stax")

    # ── Spellslinger only ─────────────────────────────────────────────────────
    if "instant or sorcery" in text and "you cast" in text and "creature" not in type_line:
        roles.append("spellslinger_only")

    # ── Voltron only ──────────────────────────────────────────────────────────
    if ("equip" in keywords or "equip" in text or "aura" in type_line) and \
       "attach" in text and "token" not in text:
        roles.append("voltron_only")

    # ── High mana low impact ─────────────────────────────────────────────────
    strong_roles = {
        "ramp", "card_draw", "targeted_removal", "boardwipe", "counterspell",
        "token_generation", "etb_synergy", "blink", "tutor", "recursion",
        "counter_synergy", "protection",
    }
    if mv >= 7 and not any(r in roles for r in strong_roles):
        roles.append("high_mana_low_impact")

    return list(dict.fromkeys(roles))  # déduplique en gardant l'ordre


# ══════════════════════════════════════════════════════════════════════════════
# 8. Deck actuel de l'utilisateur
# ══════════════════════════════════════════════════════════════════════════════

def load_current_deck(deck_file: str | None) -> set[str]:
    """Charge les cartes du deck actuel. Retourne un set de noms normalisés."""
    if not deck_file:
        return set()

    path = Path(deck_file)
    if not path.exists():
        print(f"[AVERTISSEMENT] Fichier deck introuvable : {deck_file}")
        return set()

    cards: set[str] = set()
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            content = f.read()

        # Fichier CSV seulement si l'extension est .csv ET qu'il a un délimiteur ; ou ,
        # (les .txt sont toujours traités comme texte, même s'ils contiennent des virgules)
        if path.suffix.lower() == ".csv":
            delimiter = ";" if content.count(";") > content.count(",") else ","
            try:
                reader = csv.DictReader(content.splitlines(), delimiter=delimiter)
                for row in reader:
                    for key in row:
                        if key and "card" in key.lower() and "name" in key.lower():
                            name = (row[key] or "").strip()
                            if name:
                                cards.add(normalize_card_name(name))
                            break
                if cards:
                    print(f"[Deck] {len(cards)} cartes chargées depuis {deck_file} (CSV)")
                    return cards
            except Exception:
                pass  # fallback vers le parsing texte

        # Format texte : "1 Sol Ring" ou "Sol Ring"
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            match = re.match(r"^\d+[xX]?\s+(.+)$", line)
            if match:
                cards.add(normalize_card_name(match.group(1)))
            else:
                cards.add(normalize_card_name(line))

        print(f"[Deck] {len(cards)} cartes chargées depuis {deck_file} (texte)")
    except Exception as exc:
        print(f"[AVERTISSEMENT] Erreur lecture deck : {exc}")

    return cards


# ══════════════════════════════════════════════════════════════════════════════
# 9. Statistiques decklists
# ══════════════════════════════════════════════════════════════════════════════

def build_or_load_decklist_stats(
    commander: dict[str, Any],
    rebuild_cache: bool = False,
) -> dict[str, float]:
    """
    Construit ou charge les statistiques de popularité des cartes
    dans les decklists du commandant.
    Retourne un dict {normalized_name: taux_apparition (0..1)}.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fname = _normalize_filename(commander["name"])
    cache_path = CACHE_DIR / f"decklist_stats_{fname}.json"

    if cache_path.exists() and not rebuild_cache:
        with open(cache_path, encoding="utf-8") as f:
            stats = json.load(f)
        print(f"[Decklists] Cache chargé : {len(stats)} cartes ({cache_path.name})")
        return stats

    # Chercher le dossier du commandant dans DECKLISTS_ROOT
    if not DECKLISTS_ROOT.exists():
        print("[AVERTISSEMENT] Dossier data/Decklists introuvable. Score decklist_popularity sera neutre.")
        return {}

    # Chercher un sous-dossier dont le nom correspond au commandant
    norm_cmd = _normalize_filename(commander["name"])
    commander_dir: Path | None = None

    for subdir in DECKLISTS_ROOT.iterdir():
        if subdir.is_dir():
            norm_dir = _normalize_filename(subdir.name)
            # Comparer après normalisation des deux côtés
            if norm_dir == norm_cmd or norm_cmd in norm_dir or norm_dir in norm_cmd:
                commander_dir = subdir
                break
            # Fallback : comparer le nom du dossier lowercase avec le nom normalisé sans tirets
            dir_lower = subdir.name.lower().replace("_", "").replace("-", "").replace(" ", "")
            cmd_compact = norm_cmd.replace("_", "")
            if dir_lower == cmd_compact or cmd_compact in dir_lower:
                commander_dir = subdir
                break

    if commander_dir is None:
        print(f"[AVERTISSEMENT] Aucun dossier decklists trouvé pour '{commander['name']}'.")
        print("               Score decklist_popularity sera neutre (0.5).")
        return {}

    csv_files = list(commander_dir.glob("*.csv"))
    if not csv_files:
        print(f"[AVERTISSEMENT] Aucun fichier CSV dans {commander_dir}.")
        return {}

    print(f"[Decklists] {len(csv_files)} decklists trouvées dans {commander_dir.name}")
    if len(csv_files) > 2000:
        print(f"[Decklists] Dossier très volumineux ({len(csv_files)} fichiers). Traitement en cours...")

    card_counts: dict[str, int] = defaultdict(int)
    total_decks = 0
    errors = 0

    for i, csv_path in enumerate(csv_files):
        if i > 0 and i % 500 == 0:
            print(f"[Decklists] Progression : {i}/{len(csv_files)} fichiers traités...")
        try:
            with open(csv_path, encoding="utf-8-sig", errors="replace") as f:
                content = f.read()
            delimiter = ";" if content.count(";") > content.count(",") else ","
            reader = csv.DictReader(content.splitlines(), delimiter=delimiter)
            seen_in_deck: set[str] = set()
            for row in reader:
                name = ""
                for key in row:
                    if "card" in key.lower() and "name" in key.lower():
                        name = (row[key] or "").strip()
                        break
                if not name:
                    continue
                norm = normalize_card_name(name)
                if norm and norm not in seen_in_deck:
                    seen_in_deck.add(norm)
                    card_counts[norm] += 1
            total_decks += 1
        except Exception:
            errors += 1

    if errors:
        print(f"[Decklists] {errors} fichiers ignorés (erreur de lecture).")

    if total_decks == 0:
        print("[AVERTISSEMENT] Aucune decklist valide parsée.")
        return {}

    stats = {name: count / total_decks for name, count in card_counts.items()}

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False)

    print(f"[Decklists] {len(stats)} cartes indexées sur {total_decks} decklists. Cache sauvé.")
    return stats


# ══════════════════════════════════════════════════════════════════════════════
# 10. Embeddings
# ══════════════════════════════════════════════════════════════════════════════

def load_embeddings() -> tuple[Any, list[dict[str, Any]], dict[str, int]]:
    """
    Charge les embeddings numpy et les métadonnées.
    Retourne (embeddings_array, metadata_list, name_to_index).
    """
    if not EMBEDDINGS_FILE.exists() or not METADATA_FILE.exists():
        print("[ERREUR] Les embeddings n'existent pas.")
        print("         Lance d'abord : python scripts/build_card_embeddings.py")
        sys.exit(1)

    import numpy as np

    embeddings = np.load(str(EMBEDDINGS_FILE))
    with open(METADATA_FILE, encoding="utf-8") as f:
        metadata = json.load(f)

    # Normaliser L2 si pas déjà fait
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    embeddings = embeddings / norms

    name_to_idx = {normalize_card_name(m["name"]): i for i, m in enumerate(metadata)}
    print(f"[Embeddings] {len(embeddings)} vecteurs chargés ({embeddings.shape[1]}d).")
    return embeddings, metadata, name_to_idx


def build_query_vector(
    commander: dict[str, Any],
    profile: dict[str, Any],
    user_query: str | None,
    model: Any,
) -> Any:
    """
    Encode un texte de requête enrichi combinant commandant + profil + query.
    """
    import numpy as np

    wanted_roles = ", ".join(profile.get("wanted_roles", []))
    wants = ". ".join(profile.get("wants", []))
    primary = profile.get("primary_strategy", "")
    secondary = ", ".join(profile.get("secondary_strategies", []))
    cmd_text = commander.get("oracle_text") or ""

    query_text = f"""Commander: {commander['name']}
Commander text: {cmd_text}
Primary strategy: {primary}
Secondary strategies: {secondary}
Wants: {wants}
Wanted roles: {wanted_roles}
User request: {user_query or 'general optimization'}"""

    vec = model.encode(query_text, normalize_embeddings=True, show_progress_bar=False)
    return np.array(vec, dtype=np.float32)


# ══════════════════════════════════════════════════════════════════════════════
# 11. Calcul des scores
# ══════════════════════════════════════════════════════════════════════════════

def _popularity_score_from_rate(rate: float) -> float:
    """Transforme un taux d'apparition (0..1) en score log-normalisé (0..1)."""
    return math.log1p(rate * 100) / math.log1p(100)


def compute_decklist_popularity_score(card: dict[str, Any], decklist_stats: dict[str, float]) -> float:
    """Score basé sur la fréquence d'apparition dans les decklists du commandant."""
    if not decklist_stats:
        return 0.5  # score neutre si pas de decklists
    norm = normalize_card_name(card["name"])
    rate = decklist_stats.get(norm, 0.0)
    return _popularity_score_from_rate(rate)


def compute_role_score(roles: list[str], profile: dict[str, Any]) -> float:
    """Score basé sur l'adéquation des rôles détectés avec le profil."""
    wanted = set(profile.get("wanted_roles", []))
    avoided = set(profile.get("avoided_roles", []))
    role_set = set(roles)

    bonus = len(role_set & wanted)
    penalty = len(role_set & avoided)

    if penalty > 0:
        base = max(0.0, 0.3 - 0.15 * penalty)
    elif bonus == 0:
        base = 0.2
    else:
        base = min(1.0, 0.5 + 0.25 * bonus)

    # Bonus supplémentaire si la stratégie primaire est bien couverte
    primary = profile.get("primary_strategy", "")
    if primary == "creature_etb_value" and role_set & {"etb_synergy", "token_generation", "blink"}:
        base = min(1.0, base + 0.1)

    return round(base, 4)


def _score_constraint(
    constraint: dict[str, Any],
    card: dict[str, Any],
    card_roles: list[str],
    commander: dict[str, Any],
) -> tuple[float, str | None]:
    """
    Évalue si une carte satisfait ou viole une contrainte de deckbuilding.
    Retourne (delta_score, raison_ou_None).
    delta > 0 = bonus, delta < 0 = malus.
    """
    name = constraint.get("constraint", "")
    importance = constraint.get("importance", 0.5)
    card_text = (card.get("oracle_text") or "").lower()
    type_line = (card.get("type_line") or "").lower()
    card_roles_set = set(card_roles)
    card_kw = [k.lower() for k in (card.get("keywords") or [])]

    # ── creature_power_less_than_commander_power ───────────────────────────────
    if name == "creature_power_less_than_commander_power":
        if "creature" not in type_line:
            return 0.0, None  # non-créature : neutre
        try:
            cmd_power = float(commander.get("power") or 0)
            card_power_raw = card.get("power")
            if card_power_raw is None or str(card_power_raw) in ("*", "?", ""):
                # Puissance variable → souvent faible, bonus modéré
                return round(importance * 0.15, 3), "Puissance variable (compatible)"
            card_power = float(card_power_raw)
            if card_power < cmd_power:
                return round(importance * 0.30, 3), f"Puissance {int(card_power)} < {int(cmd_power)} (contrainte respectee)"
            elif card_power == cmd_power:
                return round(importance * 0.05, 3), None  # égale, léger bonus
            else:
                # Puissance trop haute : ne peut pas être mise en jeu par le commandant
                return round(-importance * 0.25, 3), f"Puissance {int(card_power)} >= {int(cmd_power)} (hors contrainte)"
        except (TypeError, ValueError):
            return 0.0, None

    # ── needs_commander_to_attack ─────────────────────────────────────────────
    if name == "needs_commander_to_attack":
        combat_support = card_roles_set & {"combat_support", "protection", "attack_trigger"}
        if "haste" in card_kw or "haste" in card_text:
            return round(importance * 0.25, 3), "Donne haste (support d'attaque)"
        if "vigilance" in card_kw or "vigilance" in card_text:
            return round(importance * 0.20, 3), "Vigilance (attaque sans se tapper)"
        if combat_support:
            return round(importance * 0.15, 3), f"Support combat : {', '.join(combat_support)}"
        if "protection" in card_roles_set or "hexproof" in card_text or "indestructible" in card_text:
            return round(importance * 0.20, 3), "Protection du commandant pendant l'attaque"
        return 0.0, None

    # ── needs_haste_or_vigilance_support ─────────────────────────────────────
    if name == "needs_haste_or_vigilance_support":
        if "haste" in card_kw or "haste" in card_text or "gain haste" in card_text:
            return round(importance * 0.25, 3), "Donne haste au commandant"
        if "vigilance" in card_kw or "vigilance" in card_text:
            return round(importance * 0.20, 3), "Donne vigilance"
        return 0.0, None

    # ── wants_creatures_in_hand ───────────────────────────────────────────────
    if name == "wants_creatures_in_hand":
        if "card_draw" in card_roles_set:
            return round(importance * 0.20, 3), "Pioche (alimente la main en creatures)"
        if "flash" in card_kw or "flash" in card_text:
            return round(importance * 0.10, 3), "Flash (flexibilite de timing)"
        if "tutor" in card_roles_set:
            return round(importance * 0.15, 3), "Tuteur (chercher les creatures)"
        return 0.0, None

    # ── wants_high_impact_creatures ───────────────────────────────────────────
    if name == "wants_high_impact_creatures":
        if "creature" not in type_line:
            return 0.0, None
        try:
            mv = float(card.get("mana_value") or 0)
            # Grosses créatures à fort impact : MV 5+
            if mv >= 6 and card_roles_set & {"etb_synergy", "combat_support", "protection", "card_draw"}:
                return round(importance * 0.30, 3), f"Grosse creature a fort impact (MV={int(mv)})"
            if mv >= 5:
                return round(importance * 0.15, 3), None
        except (TypeError, ValueError):
            pass
        return 0.0, None

    # ── wants_card_draw_triggers ──────────────────────────────────────────────
    if name == "wants_card_draw_triggers":
        if "card_draw" in card_roles_set:
            return round(importance * 0.20, 3), "Source de pioche"
        if "draw" in card_text:
            return round(importance * 0.10, 3), None
        return 0.0, None

    # ── wants_creatures_to_enter_battlefield ──────────────────────────────────
    if name == "wants_creatures_to_enter_battlefield":
        etb_roles = card_roles_set & {"etb_synergy", "blink", "token_generation"}
        if etb_roles:
            return round(importance * 0.25, 3), f"Synergise avec les ETB : {', '.join(etb_roles)}"
        if "enters the battlefield" in card_text:
            return round(importance * 0.15, 3), "Interagit avec les entrees en jeu"
        return 0.0, None

    # ── wants_tokens ──────────────────────────────────────────────────────────
    if name == "wants_tokens":
        if "token_generation" in card_roles_set:
            return round(importance * 0.20, 3), "Cree des tokens"
        if "create" in card_text and "token" in card_text:
            return round(importance * 0.15, 3), "Cree des tokens"
        return 0.0, None

    # ── wants_instant_sorcery_density ─────────────────────────────────────────
    if name == "wants_instant_sorcery_density":
        if "instant" in type_line or "sorcery" in type_line:
            return round(importance * 0.25, 3), "Instant/Rituel (densite souhaitee)"
        return 0.0, None

    # ── wants_graveyard_filled ────────────────────────────────────────────────
    if name == "wants_graveyard_filled":
        gyard_roles = card_roles_set & {"graveyard_synergy", "sacrifice_synergy", "recursion"}
        if gyard_roles:
            return round(importance * 0.20, 3), f"Remplit/exploite le cimetiere : {', '.join(gyard_roles)}"
        if "mill" in card_text or "graveyard" in card_text:
            return round(importance * 0.10, 3), None
        return 0.0, None

    # ── wants_equipment_or_aura ───────────────────────────────────────────────
    if name == "wants_equipment_or_aura":
        if "equipment" in type_line or "aura" in type_line:
            return round(importance * 0.25, 3), "Equipement ou Aura"
        if "equip" in card_text or ("enchant" in card_text and "creature" in card_text):
            return round(importance * 0.15, 3), "Effet d'equipement ou d'aura"
        return 0.0, None

    # ── wants_sacrifice_fodder ────────────────────────────────────────────────
    if name == "wants_sacrifice_fodder":
        if "token_generation" in card_roles_set:
            return round(importance * 0.20, 3), "Cree des tokens sacrifiables"
        if "sacrifice" in card_roles_set:
            return round(importance * 0.15, 3), "Outlet de sacrifice"
        return 0.0, None

    return 0.0, None


def compute_commander_synergy_score(
    card: dict[str, Any],
    commander: dict[str, Any],
    profile: dict[str, Any],
    card_roles: list[str] | None = None,
) -> float:
    """
    Score de synergie entre la carte et le commandant.
    Combine trois sources :
      1. Similarité textuelle oracle (mots communs significatifs)
      2. Correspondance avec les 'wants' du profil
      3. Évaluation des deckbuilding_constraints du profil V2
    """
    card_text = (card.get("oracle_text") or "").lower()
    cmd_text = (commander.get("oracle_text") or "").lower()
    wants = [w.lower() for w in profile.get("wants", [])]
    roles = card_roles or []

    score = 0.0

    # ── 1. Similarité textuelle oracle ────────────────────────────────────────
    cmd_words = set(re.findall(r"\b\w{4,}\b", cmd_text))
    card_words = set(re.findall(r"\b\w{4,}\b", card_text))
    stop = {"your", "each", "when", "that", "this", "with", "from", "then",
            "they", "have", "their", "other", "another", "spell", "card",
            "target", "control", "player", "permanent", "battlefield"}
    meaningful = (cmd_words & card_words) - stop
    if meaningful:
        score += min(0.25, len(meaningful) * 0.05)

    # ── 2. Wants du profil ────────────────────────────────────────────────────
    want_hits = sum(1 for w in wants if any(part in card_text for part in w.split()))
    if want_hits:
        score += min(0.25, want_hits * 0.07)

    # ── 3. Type de carte préféré ──────────────────────────────────────────────
    preferred_types = profile.get("preferred_card_types", [])
    type_line = card.get("type_line") or ""
    if preferred_types and any(t.lower() in type_line.lower() for t in preferred_types[:2]):
        score += 0.05

    # ── 4. Deckbuilding constraints ───────────────────────────────────────────
    constraints = profile.get("deckbuilding_constraints", [])
    constraint_delta = 0.0
    for constraint in constraints:
        delta, _ = _score_constraint(constraint, card, roles, commander)
        constraint_delta += delta

    # Plafonner la contribution des contraintes à ±0.45
    constraint_delta = max(-0.45, min(0.45, constraint_delta))
    score += constraint_delta

    return round(max(0.0, min(1.0, score)), 4)


def compute_vector_score(
    card: dict[str, Any],
    embeddings: Any,
    name_to_idx: dict[str, int],
    query_vec: Any,
) -> float:
    """Score de similarité cosine entre le vecteur de requête et l'embedding de la carte."""
    import numpy as np

    norm = normalize_card_name(card["name"])
    idx = name_to_idx.get(norm)
    if idx is None:
        return 0.3  # score neutre si la carte n'a pas d'embedding
    score = float(np.dot(embeddings[idx], query_vec))
    # Cosine similarity ∈ [-1, 1] → normaliser vers [0, 1]
    return round((score + 1) / 2, 4)


def compute_edhrec_score(card: dict[str, Any]) -> float:
    """Score basé sur le classement EDHREC (popularité globale)."""
    rank = card.get("edhrec_rank")
    if rank is None:
        return 0.30
    if rank <= 100:
        return 1.00
    if rank <= 500:
        return 0.85
    if rank <= 2000:
        return 0.65
    if rank <= 10000:
        return 0.40
    return 0.20


def compute_mana_curve_score(card: dict[str, Any], roles: list[str]) -> float:
    """Score basé sur la mana value, avec exception pour les rôles importants."""
    mv = card.get("mana_value") or 0
    role_set = set(roles)
    high_impact = {"boardwipe", "tutor", "card_draw", "ramp"}

    # Exception : cartes à haut impact peuvent avoir un CMC élevé
    if mv >= 6 and role_set & high_impact:
        return 0.60

    if mv <= 1:
        return 0.75
    if mv <= 3:
        return 1.00
    if mv <= 5:
        return 0.80
    if mv == 6:
        return 0.50
    return 0.25


def compute_card_quality_score(
    card: dict[str, Any],
    roles: list[str],
    decklist_stats: dict[str, float],
) -> float:
    """Score de qualité combinant popularité, rôle et rank EDHREC."""
    edhrec = compute_edhrec_score(card)
    pop = compute_decklist_popularity_score(card, decklist_stats)
    role_set = set(roles)

    # Malus si high_mana_low_impact
    if "high_mana_low_impact" in role_set:
        return round(max(0.1, (edhrec + pop) / 2 * 0.5), 4)

    # Bonus si rôle fort
    strong = {"ramp", "card_draw", "targeted_removal", "boardwipe", "counterspell",
               "tutor", "protection", "blink", "token_generation"}
    if role_set & strong:
        return round(min(1.0, (edhrec + pop) / 2 * 1.1), 4)

    return round((edhrec + pop) / 2, 4)


def compute_final_score(
    card: dict[str, Any],
    roles: list[str],
    commander: dict[str, Any],
    profile: dict[str, Any],
    decklist_stats: dict[str, float],
    embeddings: Any,
    name_to_idx: dict[str, int],
    query_vec: Any,
) -> dict[str, Any]:
    """Calcule le score final hybride et retourne le détail complet."""
    weights = profile.get("score_weights", {})

    scores = {
        "decklist_popularity": compute_decklist_popularity_score(card, decklist_stats),
        "strategic_role": compute_role_score(roles, profile),
        "commander_synergy": compute_commander_synergy_score(card, commander, profile, roles),
        "vector_similarity": compute_vector_score(card, embeddings, name_to_idx, query_vec),
        "edhrec": compute_edhrec_score(card),
        "mana_curve": compute_mana_curve_score(card, roles),
        "card_quality": compute_card_quality_score(card, roles, decklist_stats),
    }

    final = sum(weights.get(k, 0) * v for k, v in scores.items())

    # Générer les raisons
    reasons: list[str] = []
    if scores["strategic_role"] >= 0.6:
        matching = set(roles) & set(profile.get("wanted_roles", []))
        if matching:
            reasons.append(f"Rôles utiles pour la stratégie : {', '.join(matching)}")
    if scores["decklist_popularity"] >= 0.5 and decklist_stats:
        reasons.append("Bonne présence dans les decklists similaires")
    elif not decklist_stats:
        reasons.append("Score decklists non disponible (neutre)")
    if scores["commander_synergy"] >= 0.5:
        reasons.append("Bonne synergie avec le texte du commandant")
    # Raisons issues des contraintes deckbuilding
    for constraint in profile.get("deckbuilding_constraints", []):
        _, reason = _score_constraint(constraint, card, roles, commander)
        if reason:
            reasons.append(reason)
    if scores["edhrec"] >= 0.65:
        reasons.append(f"Très bien classé EDHREC (rank {card.get('edhrec_rank', 'N/A')})")
    if scores["mana_curve"] >= 0.8:
        reasons.append(f"Mana value cohérente ({card.get('mana_value', '?')})")
    if "high_mana_low_impact" in roles:
        reasons.append("Attention : mana value élevée sans rôle fort")
    avoided_match = set(roles) & set(profile.get("avoided_roles", []))
    if avoided_match:
        reasons.append(f"Rôles à éviter détectés : {', '.join(avoided_match)}")

    return {
        "name": card["name"],
        "mana_cost": card.get("mana_cost") or "",
        "mana_value": card.get("mana_value") or 0,
        "type_line": card.get("type_line") or "",
        "edhrec_rank": card.get("edhrec_rank"),
        "final_score": round(final, 4),
        "score_details": {k: round(v, 4) for k, v in scores.items()},
        "roles": roles,
        "reasons": reasons,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 12. Filtres post-scoring
# ══════════════════════════════════════════════════════════════════════════════

def _should_exclude_post_scoring(result: dict[str, Any], profile: dict[str, Any]) -> bool:
    """Exclut uniquement les cartes vraiment hors-sujet (score très faible)."""
    role_set = set(result["roles"])

    # Exclure uniquement high_mana_low_impact sans aucun rôle fort
    if "high_mana_low_impact" in role_set and result["final_score"] < 0.35:
        return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# 13. Groupement par rôle
# ══════════════════════════════════════════════════════════════════════════════

def group_results_by_role(results: list[dict[str, Any]], top_per_role: int = 10) -> dict[str, list[dict[str, Any]]]:
    """Groupe les résultats par rôle en évitant les doublons dans l'affichage."""
    groups: dict[str, list[dict[str, Any]]] = {
        "top_global": results[:top_per_role],
        "ramp": [],
        "card_draw": [],
        "removal": [],
        "protection": [],
        "synergy": [],
        "high_impact": [],
    }

    role_map = {
        "ramp": ["ramp"],
        "card_draw": ["card_draw"],
        "removal": ["targeted_removal", "boardwipe"],
        "protection": ["protection", "counterspell"],
        "synergy": ["etb_synergy", "token_generation", "blink", "counter_synergy",
                    "tribal_synergy", "anthem"],
        "high_impact": ["tutor", "boardwipe", "card_draw"],
    }

    # Cartes déjà dans le top global
    top_names = {r["name"] for r in groups["top_global"]}

    for group_name, target_roles in role_map.items():
        seen: set[str] = set()
        for result in results:
            if len(groups[group_name]) >= top_per_role:
                break
            if result["name"] in top_names:
                continue
            if result["name"] in seen:
                continue
            if any(r in result["roles"] for r in target_roles):
                groups[group_name].append(result)
                seen.add(result["name"])

    return groups


# ══════════════════════════════════════════════════════════════════════════════
# 14. Affichage console
# ══════════════════════════════════════════════════════════════════════════════

def _print_card_line(result: dict[str, Any], idx: int) -> None:
    rank_str = f"EDHREC#{result['edhrec_rank']}" if result['edhrec_rank'] else "EDHREC:N/A"
    roles_str = ", ".join(result["roles"][:3]) if result["roles"] else "aucun"
    print(f"  {idx:>3}. [{result['final_score']:.3f}] {result['name']:<40} "
          f"MV={result['mana_value']:.0f}  {rank_str:<15} rôles: {roles_str}")


def print_results(
    groups: dict[str, list[dict[str, Any]]],
    commander: dict[str, Any],
    profile: dict[str, Any],
) -> None:
    """Affiche les résultats groupés dans la console."""
    sep = "=" * 80
    print(f"\n{sep}")
    print(f"  RECOMMANDATIONS V2 -- {commander['name']}")
    print(f"  Strategie : {profile.get('primary_strategy', '?')} | "
          f"Couleurs : {commander.get('color_identity', [])}")
    print(sep)

    section_labels = {
        "top_global": "TOP GLOBAL",
        "ramp": "TOP RAMP",
        "card_draw": "TOP PIOCHE",
        "removal": "TOP REMOVAL",
        "protection": "TOP PROTECTION",
        "synergy": "TOP SYNERGIES",
        "high_impact": "TOP HIGH-IMPACT",
    }

    for key, label in section_labels.items():
        cards = groups.get(key, [])
        if not cards:
            continue
        print(f"\n  -- {label} ({'hors Top Global' if key != 'top_global' else ''}) --")
        for i, result in enumerate(cards, 1):
            _print_card_line(result, i)

    print(f"\n{sep}\n")


# ══════════════════════════════════════════════════════════════════════════════
# 15. Exports
# ══════════════════════════════════════════════════════════════════════════════

def export_results_json(
    results: list[dict[str, Any]],
    groups: dict[str, list[dict[str, Any]]],
    commander: dict[str, Any],
    profile: dict[str, Any],
    output_path: str | None,
) -> Path:
    """Exporte les résultats en JSON."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        fname = _normalize_filename(commander["name"])
        path = OUTPUT_DIR / f"recommendations_v2_{fname}.json"

    payload = {
        "commander": commander["name"],
        "color_identity": commander.get("color_identity", []),
        "profile": profile.get("primary_strategy"),
        "total_recommendations": len(results),
        "groups": {k: v for k, v in groups.items()},
        "all_results": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[Export] JSON -> {path}")
    return path


def export_results_csv(
    results: list[dict[str, Any]],
    commander: dict[str, Any],
    output_path: str | None,
) -> Path:
    """Exporte les résultats en CSV (additions uniquement — retraits gérés par server.py)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        fname = _normalize_filename(commander["name"])
        path = OUTPUT_DIR / f"recommendations_v2_{fname}.csv"

    fieldnames = [
        "name", "final_score", "mana_value", "mana_cost", "type_line", "edhrec_rank",
        "roles", "decklist_popularity", "strategic_role", "commander_synergy",
        "vector_similarity", "edhrec", "mana_curve", "card_quality", "reasons",
        "section",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        # Additions
        for r in results:
            d = r.get("score_details", {})
            writer.writerow({
                "name": r["name"],
                "final_score": r["final_score"],
                "mana_value": r.get("mana_value", ""),
                "mana_cost": r.get("mana_cost", ""),
                "type_line": r.get("type_line", ""),
                "edhrec_rank": r.get("edhrec_rank", ""),
                "roles": "|".join(r.get("roles", [])),
                "decklist_popularity": d.get("decklist_popularity", ""),
                "strategic_role": d.get("strategic_role", ""),
                "commander_synergy": d.get("commander_synergy", ""),
                "vector_similarity": d.get("vector_similarity", ""),
                "edhrec": d.get("edhrec", ""),
                "mana_curve": d.get("mana_curve", ""),
                "card_quality": d.get("card_quality", ""),
                "reasons": " | ".join(r.get("reasons", [])),
                "section": "add",
            })
    print(f"[Export] CSV  -> {path}")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# 16. Main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recommandations vectorielles V2 — Moteur hybride MTG Commander"
    )
    parser.add_argument("--commander", required=True, help="Nom exact du commandant")
    parser.add_argument("--query", default=None, help="Demande utilisateur optionnelle")
    parser.add_argument("--deck-file", default=None, help="Chemin vers le deck actuel")
    parser.add_argument("--top", type=int, default=50, help="Nombre de recommandations (défaut: 50)")
    parser.add_argument("--role", default=None, help="Filtrer par rôle (ex: ramp, card_draw)")
    parser.add_argument("--output-json", default=None, help="Chemin de sortie JSON")
    parser.add_argument("--output-csv", default=None, help="Chemin de sortie CSV")
    parser.add_argument("--rebuild-cache", action="store_true", help="Reconstruire le cache decklists")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  ManaMind -- Recommandations Vectorielles V2")
    print("=" * 60)

    # ── 1. Environnement & DB ──────────────────────────────────────────────────
    db_url = load_env()
    engine = get_database_engine(db_url)

    # ── 2. Deck actuel ────────────────────────────────────────────────────────
    current_deck = load_current_deck(args.deck_file)

    # ── 3. Commandant ─────────────────────────────────────────────────────────
    commander = load_commander(engine, args.commander)

    # ── 4. Profil stratégique (nouveau module) ────────────────────────────────
    profile = load_or_create_commander_profile(
        commander,
        force_rebuild=args.rebuild_cache,
    )
    # Affichage console du profil
    conf = profile.get("strategy_confidence", "?")
    secondary = profile.get("secondary_strategies", [])
    print(f"[Profil] Strategie : {profile.get('primary_strategy')} (conf={conf})")
    if secondary:
        print(f"[Profil] Secondaires : {', '.join(secondary[:3])}")
    constraints = profile.get("deckbuilding_constraints", [])
    if constraints:
        print(f"[Profil] Contraintes : {', '.join(c['constraint'] for c in constraints[:3])}")
    warnings = profile.get("evidence", {}).get("warnings", [])
    for w in warnings:
        print(f"[Profil] AVERTISSEMENT : {w}")

    # ── 5. Cartes candidates (filtrées) ───────────────────────────────────────
    candidates = load_cards_from_database(engine, commander, current_deck)

    # ── 6. Embeddings ─────────────────────────────────────────────────────────
    embeddings, metadata, name_to_idx = load_embeddings()

    # ── 7. Decklists stats ────────────────────────────────────────────────────
    decklist_stats = build_or_load_decklist_stats(commander, rebuild_cache=args.rebuild_cache)

    # ── 8. Modèle d'embedding ─────────────────────────────────────────────────
    print("[Modèle] Chargement BAAI/bge-m3...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-m3", device="cpu")
    query_vec = build_query_vector(commander, profile, args.query, model)
    print("[Modèle] Vecteur de requête construit.")

    # ── 9. Scoring ────────────────────────────────────────────────────────────
    print(f"[Scoring] Évaluation de {len(candidates)} cartes candidates...")
    results: list[dict[str, Any]] = []

    for card in candidates:
        roles = infer_card_roles(card)

        # Filtre optionnel par rôle CLI
        if args.role and args.role not in roles:
            continue

        result = compute_final_score(
            card, roles, commander, profile,
            decklist_stats, embeddings, name_to_idx, query_vec,
        )

        if _should_exclude_post_scoring(result, profile):
            continue

        results.append(result)

    # Tri par score final décroissant
    results.sort(key=lambda x: x["final_score"], reverse=True)
    top_results = results[:args.top]

    print(f"[Scoring] {len(top_results)} recommandations générées (sur {len(results)} scorées).")

    # ── 10. Groupement et affichage ───────────────────────────────────────────
    groups = group_results_by_role(top_results, top_per_role=10)
    print_results(groups, commander, profile)

    # ── 11. Exports (retraits calculés par server.py, pas ici) ────────────────
    json_path = export_results_json(top_results, groups, commander, profile, args.output_json)
    csv_path = export_results_csv(top_results, commander, args.output_csv)

    print(f"\n[Terminé] {len(top_results)} recommandations pour '{commander['name']}'")
    print(f"          JSON : {json_path}")
    print(f"          CSV  : {csv_path}")
    print()


if __name__ == "__main__":
    main()
