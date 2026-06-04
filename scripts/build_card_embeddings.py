#!/usr/bin/env python3
"""
scripts/build_card_embeddings.py
=================================
Génère des embeddings vectoriels pour les cartes MTG Commander légales.

Flux :
  1. Connexion PostgreSQL via DATABASE_URL (.env)
  2. Lecture des cartes (legal_commander = true par défaut)
  3. Construction d'un texte descriptif par carte
  4. Génération des embeddings avec BAAI/bge-m3 (modèle local, gratuit)
  5. Normalisation L2 (produit scalaire = cosine similarity)
  6. Sauvegarde dans data/embeddings/card_embeddings.npy
  7. Sauvegarde des métadonnées dans data/embeddings/card_metadata.json

Usage :
  python scripts/build_card_embeddings.py
  python scripts/build_card_embeddings.py --batch-size 16   # CPU lent
  python scripts/build_card_embeddings.py --batch-size 64   # GPU / CPU rapide
  python scripts/build_card_embeddings.py --all-cards       # toutes les cartes

Prérequis :
  uv add sentence-transformers
  DATABASE_URL dans .env (PostgreSQL démarré)
  Cartes importées via scripts/import_scryfall_cards.py

Avertissement :
  Premier lancement : téléchargement du modèle BAAI/bge-m3 (~1.5 Go).
  Génération des embeddings : 10-60 min selon le CPU (CPU sans GPU).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ── PYTHONPATH : rend src.manamind importable ─────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

# ── Chemins de sortie ──────────────────────────────────────────────────────────
EMBEDDINGS_DIR = ROOT / "data" / "embeddings"
EMBEDDINGS_FILE = EMBEDDINGS_DIR / "card_embeddings.npy"
METADATA_FILE = EMBEDDINGS_DIR / "card_metadata.json"

MODEL_NAME = "BAAI/bge-m3"
DEFAULT_BATCH_SIZE = 32


# ── Construction du texte représentatif d'une carte ───────────────────────────
def build_card_text(card: Any) -> str:
    """
    Transforme une carte en texte structuré pour l'embedding.
    Chaque champ est explicitement labellisé pour que le modèle
    comprenne le contexte sémantique de chaque valeur.
    """
    parts: list[str] = [f"Name: {card.name}"]

    if card.mana_cost:
        parts.append(f"Mana cost: {card.mana_cost}")

    if card.mana_value is not None:
        parts.append(f"Mana value: {int(card.mana_value)}")

    if card.type_line:
        parts.append(f"Type: {card.type_line}")

    if card.oracle_text:
        # L'oracle text est la source principale de sémantique
        parts.append(f"Rules: {card.oracle_text}")

    colors = card.colors or []
    parts.append(f"Colors: {', '.join(colors) if colors else 'Colorless'}")

    color_identity = card.color_identity or []
    parts.append(
        f"Color identity: {', '.join(color_identity) if color_identity else 'Colorless'}"
    )

    keywords = card.keywords or []
    if keywords:
        parts.append(f"Keywords: {', '.join(keywords)}")

    if card.edhrec_rank:
        parts.append(f"EDHREC rank: {card.edhrec_rank}")

    parts.append(f"Legal in Commander: {card.legal_commander}")

    return " | ".join(parts)


def build_card_metadata(card: Any) -> dict[str, Any]:
    """Extrait les métadonnées utiles pour l'affichage des résultats."""
    return {
        "id": card.id,
        "oracle_id": card.oracle_id,
        "name": card.name,
        "mana_cost": card.mana_cost,
        "mana_value": card.mana_value,
        "type_line": card.type_line,
        "colors": card.colors or [],
        "color_identity": card.color_identity or [],
        "keywords": card.keywords or [],
        "legal_commander": card.legal_commander,
        "edhrec_rank": card.edhrec_rank,
    }


def load_cards_from_db(commander_only: bool = True) -> list[Any]:
    """
    Charge les cartes depuis PostgreSQL.
    Trie par popularité EDHREC puis par nom pour un ordre stable.
    """
    from sqlalchemy import select

    from src.manamind.db.engine import SessionLocal
    from src.manamind.db.models.card import Card

    stmt = select(Card)
    if commander_only:
        stmt = stmt.where(Card.legal_commander.is_(True))
    stmt = stmt.order_by(Card.edhrec_rank.asc().nulls_last(), Card.name)

    with SessionLocal() as session:
        return list(session.execute(stmt).scalars().all())


def main(batch_size: int = DEFAULT_BATCH_SIZE, commander_only: bool = True) -> None:
    """Pipeline complet : DB → textes → embeddings → fichiers."""

    # ── 1. Vérification de la connexion DB ────────────────────────────────────
    from src.manamind.db.engine import check_connection

    print("🔌 Vérification de la connexion PostgreSQL...")
    if not check_connection():
        print(
            "\n❌  Connexion impossible.\n"
            "    Vérifie que DATABASE_URL est défini dans .env\n"
            "    et que PostgreSQL est démarré.\n"
            "    Exemple .env :\n"
            "      DATABASE_URL=postgresql+psycopg2://user:pwd@localhost:5432/manamind"
        )
        sys.exit(1)
    print("✓  Connexion PostgreSQL OK\n")

    # ── 2. Création du dossier de sortie ──────────────────────────────────────
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)

    # ── 3. Chargement des cartes ──────────────────────────────────────────────
    filter_label = "légales Commander" if commander_only else "toutes"
    print(f"📚 Chargement des cartes ({filter_label}) depuis la base...")

    try:
        cards = load_cards_from_db(commander_only=commander_only)
    except Exception as exc:
        print(f"❌  Erreur lors de la lecture : {exc}")
        sys.exit(1)

    if not cards:
        print(
            "❌  Aucune carte trouvée.\n"
            "    Lance d'abord : python scripts/import_scryfall_cards.py"
        )
        sys.exit(1)

    print(f"✓  {len(cards):,} cartes chargées\n")

    # ── 4. Construction des textes et métadonnées ─────────────────────────────
    print("📝 Construction des textes descriptifs...")
    texts = [build_card_text(card) for card in cards]
    metadata = [build_card_metadata(card) for card in cards]
    print(f"✓  Textes construits (exemple : {texts[0][:80]}...)\n")

    # ── 5. Chargement du modèle d'embeddings ──────────────────────────────────
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print(
            "❌  sentence-transformers non installé.\n"
            "    Lance : uv add sentence-transformers"
        )
        sys.exit(1)

    print(f"🤖 Chargement du modèle : {MODEL_NAME}")
    print("   (Premier lancement : téléchargement ~1.5 Go — patiente...)")
    model = SentenceTransformer(MODEL_NAME)
    print("✓  Modèle prêt\n")

    # ── 6. Génération des embeddings ──────────────────────────────────────────
    print(f"⚡ Génération des embeddings (batch_size={batch_size})...")
    print("   Durée estimée : 10-60 min sur CPU selon la quantité de cartes.\n")

    import numpy as np

    raw = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,  # normalisation L2 : dot product = cosine similarity
        convert_to_numpy=True,
    )

    # Garantir un ndarray même si sentence-transformers retourne un tensor
    embeddings: np.ndarray = np.array(raw, dtype=np.float32)
    print(f"\n✓  Embeddings : shape={embeddings.shape}, dtype={embeddings.dtype}\n")

    # ── 7. Sauvegarde ─────────────────────────────────────────────────────────
    print("💾 Sauvegarde...")

    np.save(EMBEDDINGS_FILE, embeddings)
    print(f"✓  Embeddings → {EMBEDDINGS_FILE}  "
          f"({EMBEDDINGS_FILE.stat().st_size / 1e6:.1f} Mo)")

    METADATA_FILE.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓  Métadonnées → {METADATA_FILE}  ({len(metadata):,} cartes)\n")

    print(
        f"✅ Terminé !\n"
        f"   {len(cards):,} cartes | dimension {embeddings.shape[1]}d\n"
        f"   Lance maintenant : python scripts/test_vector_recommendation.py"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Génère les embeddings vectoriels des cartes MTG."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Taille des batches pour l'encodage (défaut : {DEFAULT_BATCH_SIZE}). "
             "Réduire si mémoire insuffisante.",
    )
    parser.add_argument(
        "--all-cards",
        action="store_true",
        help="Inclure toutes les cartes, pas seulement celles légales en Commander.",
    )
    args = parser.parse_args()
    main(batch_size=args.batch_size, commander_only=not args.all_cards)
