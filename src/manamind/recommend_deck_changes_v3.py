#!/usr/bin/env python3
"""
ManaMind — Recommend Deck Changes V3 (IA Vectorielle)
======================================================
Moteur de recommandation par similarité vectorielle.

Stratégie :
  1. Charger les embeddings pré-calculés (data/embeddings/)
  2. Parser la decklist utilisateur
  3. Calculer le "centroïde" du deck : moyenne des vecteurs des cartes présentes
  4. Le blender avec le vecteur du commandant (40% commandant + 60% deck)
  5. Additions : cartes du dataset les plus proches du centroïde, absentes du deck
  6. Retraits : cartes du deck les plus éloignées du centroïde (hors-thème)
  7. Exporter un CSV compatible avec l'affichage existant

Usage (CLI) :
  python src/manamind/recommend_deck_changes_v3.py \\
      --input  uploads/my_deck.txt \\
      --output outputs/recommendations_v3_my_deck.csv

Prérequis :
  Avoir lancé python scripts/build_card_embeddings.py au préalable.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

# ── PYTHONPATH ────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from manamind.recommend_deck_changes import (   # noqa: E402
    BASIC_LANDS,
    normalize_name,
    parse_decklist_text,
)

# ── Chemins ────────────────────────────────────────────────────────────────────
EMBEDDINGS_DIR = _ROOT / "data" / "embeddings"
EMBEDDINGS_FILE = EMBEDDINGS_DIR / "card_embeddings.npy"
METADATA_FILE = EMBEDDINGS_DIR / "card_metadata.json"

MODEL_NAME = "BAAI/bge-m3"
BASIC_LANDS_NORM: frozenset[str] = frozenset(normalize_name(c) for c in BASIC_LANDS)

# Pondération commandant vs centroïde deck pour le vecteur de requête
COMMANDER_WEIGHT = 0.40
DECK_WEIGHT = 0.60


# ── Chargement des ressources ─────────────────────────────────────────────────
def load_embeddings() -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Charge les vecteurs et métadonnées pré-calculés."""
    if not EMBEDDINGS_FILE.exists():
        print(
            f"[V3] ❌  Embeddings introuvables : {EMBEDDINGS_FILE}\n"
            "       Lance d'abord : python scripts/build_card_embeddings.py",
            file=sys.stderr,
        )
        sys.exit(1)

    embeddings = np.load(EMBEDDINGS_FILE).astype(np.float32)

    with METADATA_FILE.open("r", encoding="utf-8") as f:
        metadata: list[dict[str, Any]] = json.load(f)

    return embeddings, metadata


def load_model() -> Any:
    """Charge le modèle sentence-transformers."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print(
            "[V3] ❌  sentence-transformers non installé.\n"
            "       Lance : uv add sentence-transformers",
            file=sys.stderr,
        )
        sys.exit(1)

    return SentenceTransformer(MODEL_NAME)


# ── Construction du vecteur de requête ────────────────────────────────────────
def build_query_vector(
    user_cards: set[str],
    commander: str | None,
    embeddings: np.ndarray,
    metadata: list[dict[str, Any]],
    model: Any,
) -> np.ndarray:
    """
    Calcule le vecteur de requête représentant le deck utilisateur.

    Stratégie :
      - Centroïde des cartes du deck présentes dans les embeddings
      - Blendé avec le vecteur du commandant si disponible
      - Fallback : encodage textuel du nom du commandant si aucun vecteur trouvé
    """
    # Index nom normalisé → position dans l'array d'embeddings
    name_to_idx: dict[str, int] = {
        normalize_name(m["name"]).lower(): i for i, m in enumerate(metadata)
    }

    # Vecteurs des cartes du deck présentes dans les embeddings
    deck_indices: list[int] = []
    for card in user_cards:
        key = normalize_name(card).lower()
        if key in name_to_idx:
            deck_indices.append(name_to_idx[key])

    # Centroïde du deck
    if deck_indices:
        deck_vecs = embeddings[deck_indices]
        centroid: np.ndarray = deck_vecs.mean(axis=0)
    else:
        # Aucune carte du deck dans les embeddings → requête texte pure
        query_text = f"Commander: {commander}" if commander else "Magic The Gathering Commander deck"
        centroid = model.encode(query_text, normalize_embeddings=True, convert_to_numpy=True)

    # Vecteur du commandant
    cmd_vec: np.ndarray | None = None
    if commander:
        cmd_key = normalize_name(commander).lower()
        if cmd_key in name_to_idx:
            cmd_vec = embeddings[name_to_idx[cmd_key]]
        else:
            # Commandant absent des embeddings → encodage textuel
            cmd_text = f"Commander card: {commander}"
            cmd_vec = model.encode(cmd_text, normalize_embeddings=True, convert_to_numpy=True)

    # Fusion commandant + centroïde deck
    if cmd_vec is not None:
        query_vec = COMMANDER_WEIGHT * cmd_vec + DECK_WEIGHT * centroid
    else:
        query_vec = centroid

    # Re-normalisation L2
    norm = float(np.linalg.norm(query_vec))
    if norm > 0:
        query_vec = query_vec / norm

    return query_vec.astype(np.float32)


# ── Recommandations ────────────────────────────────────────────────────────────
def recommend_additions_v3(
    user_cards: set[str],
    commander: str | None,
    embeddings: np.ndarray,
    metadata: list[dict[str, Any]],
    query_vec: np.ndarray,
    limit: int = 20,
) -> list[tuple[str, float]]:
    """
    Retourne les cartes les plus proches du centroïde deck,
    absentes du deck utilisateur.

    Returns : list of (card_name, cosine_score)
    """
    user_norm: set[str] = {normalize_name(c).lower() for c in user_cards}
    cmd_norm = normalize_name(commander).lower() if commander else None

    # Scores de similarité cosine (embeddings déjà normalisés)
    scores: np.ndarray = embeddings @ query_vec

    results: list[tuple[str, float]] = []
    for idx in np.argsort(scores)[::-1]:
        card = metadata[idx]
        name_key = normalize_name(card["name"]).lower()

        # Exclure : cartes du deck, commandant, terrains de base
        if name_key in user_norm:
            continue
        if name_key == cmd_norm:
            continue
        if name_key in BASIC_LANDS_NORM:
            continue

        results.append((card["name"], float(scores[idx])))
        if len(results) >= limit:
            break

    return results


def recommend_removals_v3(
    user_cards: set[str],
    commander: str | None,
    embeddings: np.ndarray,
    metadata: list[dict[str, Any]],
    query_vec: np.ndarray,
    limit: int = 20,
) -> list[tuple[str, float]]:
    """
    Retourne les cartes du deck les plus éloignées du centroïde
    (cartes hors-thème, candidates au retrait).

    Returns : list of (card_name, cosine_score) — score le plus bas = plus hors-thème
    """
    name_to_idx: dict[str, int] = {
        normalize_name(m["name"]).lower(): i for i, m in enumerate(metadata)
    }
    cmd_norm = normalize_name(commander).lower() if commander else None

    scored: list[tuple[str, float]] = []
    for card in user_cards:
        key = normalize_name(card).lower()

        # Exclure commandant et terrains de base
        if key == cmd_norm or key in BASIC_LANDS_NORM:
            continue

        if key in name_to_idx:
            idx = name_to_idx[key]
            score = float(embeddings[idx] @ query_vec)
        else:
            score = 0.0  # Carte inconnue → score nul = candidate au retrait

        scored.append((card, score))

    # Trier par score croissant (les plus éloignés en premier)
    scored.sort(key=lambda x: x[1])
    return scored[:limit]


# ── Écriture CSV ──────────────────────────────────────────────────────────────
def save_recommendations_v3(
    output_path: Path,
    commander: str | None,
    additions: list[tuple[str, float]],
    removals: list[tuple[str, float]],
) -> None:
    """
    Écrit le CSV V3 compatible avec l'affichage existant (même format V1/V2).
    Colonnes supplémentaires après la 5e sont ignorées par l'interface.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Section", "Card Name", "Score", "Support", "Deck Frequency",
            "Vector Score", "Algorithm",
        ])
        writer.writerow(["Commander", commander or "unknown", "", "", "", "", "v3"])
        writer.writerow([])

        writer.writerow(["Additions", "", "", "", "", "", ""])
        for name, score in additions:
            display_score = round(score * 2000)  # mis à l'échelle V1/V2
            writer.writerow(["add", name, display_score, "", "", round(score, 6), "v3"])

        writer.writerow([])

        writer.writerow(["Removals", "", "", "", "", "", ""])
        for name, score in removals:
            writer.writerow(["remove", name, "", "", "", round(score, 6), "v3"])


# ── Pipeline principal ────────────────────────────────────────────────────────
def run(input_path: Path, output_path: Path, limit: int = 20) -> None:
    """Lance le pipeline complet V3."""

    # 1. Parsing de la decklist
    try:
        cards_dict, commander = parse_decklist_text(input_path)
    except Exception as exc:
        print(f"[V3] Erreur de parsing : {exc}", file=sys.stderr)
        save_recommendations_v3(output_path, None, [], [])
        return

    user_cards: set[str] = set(cards_dict.keys())

    if not user_cards:
        print("[V3] Decklist vide ou mal formatée.", file=sys.stderr)
        save_recommendations_v3(output_path, commander, [], [])
        return

    # 2. Chargement des embeddings
    embeddings, metadata = load_embeddings()

    # 3. Chargement du modèle
    model = load_model()

    # 4. Vecteur de requête (centroïde deck + commandant)
    query_vec = build_query_vector(user_cards, commander, embeddings, metadata, model)

    # 5. Recommandations
    additions = recommend_additions_v3(
        user_cards, commander, embeddings, metadata, query_vec, limit
    )
    removals = recommend_removals_v3(
        user_cards, commander, embeddings, metadata, query_vec, limit
    )

    # 6. Export CSV
    save_recommendations_v3(output_path, commander, additions, removals)

    print(
        f"[V3] {len(additions)} ajouts et {len(removals)} retraits "
        f"écrits dans {output_path}"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="ManaMind — Recommandations V3 (IA Vectorielle, BAAI/bge-m3)"
    )
    parser.add_argument("--input",  required=True, type=Path, help="Decklist utilisateur (.txt)")
    parser.add_argument("--output", required=True, type=Path, help="Fichier CSV de sortie")
    parser.add_argument("--limit",  type=int, default=20, help="Recommandations par catégorie")
    args = parser.parse_args()
    run(args.input, args.output, args.limit)


if __name__ == "__main__":
    main()
