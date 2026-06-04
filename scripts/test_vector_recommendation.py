#!/usr/bin/env python3
"""
scripts/test_vector_recommendation.py
=======================================
Test du moteur de recommandation vectorielle ManaMind V3.

Prend une requête en langage naturel et retourne les cartes MTG
les plus proches sémantiquement, via produit scalaire sur embeddings
normalisés (= cosine similarity).

Prérequis :
  Avoir lancé build_card_embeddings.py au moins une fois.

Usage :
  python scripts/test_vector_recommendation.py
  python scripts/test_vector_recommendation.py --query "cartes de pioche"
  python scripts/test_vector_recommendation.py --query "ramp artifacts" --top 10
  python scripts/test_vector_recommendation.py --query "board wipe blanc"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

# ── PYTHONPATH ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMBEDDINGS_DIR = ROOT / "data" / "embeddings"
EMBEDDINGS_FILE = EMBEDDINGS_DIR / "card_embeddings.npy"
METADATA_FILE = EMBEDDINGS_DIR / "card_metadata.json"

MODEL_NAME = "BAAI/bge-m3"

# Requête de démonstration par défaut
DEFAULT_QUERY = (
    "Je cherche des cartes Commander pour améliorer un deck Galadriel. "
    "Je veux plus de pioche, plus de ramp, et des cartes qui synergisent "
    "avec les créatures qui arrivent en jeu."
)


def load_embeddings_and_metadata() -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Charge les vecteurs et les métadonnées depuis le disque."""
    if not EMBEDDINGS_FILE.exists():
        print(
            f"❌  Fichier introuvable : {EMBEDDINGS_FILE}\n"
            "    Lance d'abord : python scripts/build_card_embeddings.py"
        )
        sys.exit(1)

    if not METADATA_FILE.exists():
        print(
            f"❌  Fichier introuvable : {METADATA_FILE}\n"
            "    Lance d'abord : python scripts/build_card_embeddings.py"
        )
        sys.exit(1)

    embeddings = np.load(EMBEDDINGS_FILE).astype(np.float32)

    with METADATA_FILE.open("r", encoding="utf-8") as f:
        metadata: list[dict[str, Any]] = json.load(f)

    if len(embeddings) != len(metadata):
        print(
            f"❌  Incohérence : {len(embeddings)} vecteurs "
            f"mais {len(metadata)} cartes en métadonnées.\n"
            "    Relance build_card_embeddings.py pour reconstruire."
        )
        sys.exit(1)

    return embeddings, metadata


def search(
    query: str,
    embeddings: np.ndarray,
    metadata: list[dict[str, Any]],
    model: Any,
    top_k: int = 20,
) -> list[tuple[float, dict[str, Any]]]:
    """
    Encode la requête et retourne les top_k cartes par similarité cosine.
    Les embeddings étant normalisés L2, le produit scalaire = cosine similarity.
    """
    query_vec: np.ndarray = model.encode(
        query,
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)

    # Produit scalaire vectorisé : shape (n_cards,)
    scores: np.ndarray = embeddings @ query_vec

    # Indices triés par score décroissant
    top_indices = np.argsort(scores)[::-1][:top_k]

    return [(float(scores[i]), metadata[i]) for i in top_indices]


def print_results(
    results: list[tuple[float, dict[str, Any]]],
    query: str,
    top_k: int,
) -> None:
    """Affiche les résultats de façon lisible dans le terminal."""
    sep = "─" * 72

    print(f"\n{sep}")
    print(f"  REQUÊTE : {query}")
    print(f"{sep}")
    print(f"  TOP {top_k} RECOMMANDATIONS VECTORIELLES  (BAAI/bge-m3)\n")

    for rank, (score, card) in enumerate(results, start=1):
        name: str = card.get("name", "?")
        type_line: str = card.get("type_line") or "—"
        mana_value = card.get("mana_value")
        color_identity: list[str] = card.get("color_identity") or []
        edhrec_rank = card.get("edhrec_rank")
        mana_cost: str = card.get("mana_cost") or ""

        ci_str = "".join(color_identity) if color_identity else "C"
        mv_str = f"CMC {int(mana_value)}" if mana_value is not None else "CMC —"
        er_str = f"EDHREC #{edhrec_rank:,}" if edhrec_rank else "EDHREC —"
        mc_str = f"  {mana_cost}" if mana_cost else ""

        print(
            f"  {rank:2d}. [{score:.4f}]  {name}{mc_str}\n"
            f"       {type_line}\n"
            f"       {mv_str}  |  {ci_str}  |  {er_str}\n"
        )

    print(sep)
    print(
        f"\n  💡 Conseil : affine la requête pour des résultats plus ciblés.\n"
        f"     Exemple : python scripts/test_vector_recommendation.py "
        f'--query "draw cards elves"\n'
    )


def main(query: str = DEFAULT_QUERY, top_k: int = 20) -> None:
    """Pipeline : chargement → modèle → requête → affichage."""

    # ── Chargement des données ────────────────────────────────────────────────
    print("📂 Chargement des embeddings et métadonnées...")
    embeddings, metadata = load_embeddings_and_metadata()
    print(f"✓  {len(metadata):,} cartes  |  dimension {embeddings.shape[1]}d\n")

    # ── Chargement du modèle ──────────────────────────────────────────────────
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print(
            "❌  sentence-transformers non installé.\n"
            "    Lance : uv add sentence-transformers"
        )
        sys.exit(1)

    print(f"🤖 Chargement du modèle : {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)
    print("✓  Modèle prêt\n")

    # ── Recherche ─────────────────────────────────────────────────────────────
    print(f"🔍 Encodage et recherche...")
    results = search(query, embeddings, metadata, model, top_k=top_k)

    # ── Affichage ─────────────────────────────────────────────────────────────
    print_results(results, query, top_k)


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Teste le moteur de recommandation vectorielle ManaMind V3."
    )
    parser.add_argument(
        "--query",
        type=str,
        default=DEFAULT_QUERY,
        help="Requête en langage naturel (français ou anglais).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Nombre de résultats à afficher (défaut : 20).",
    )
    args = parser.parse_args()
    main(query=args.query, top_k=args.top)
