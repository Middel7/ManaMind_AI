#!/usr/bin/env python3
"""
cluster_archetype.py

Re-clustering ciblé sur un sous-ensemble de cartes appartenant à un archétype donné.
Utilise les embeddings Card2Vec globaux (pas de ré-entraînement), mais relance
UMAP + HDBSCAN sur les cartes des commandants de l'archétype uniquement.

Les nouveaux clusters obtiennent des IDs >= 1000 pour ne pas entrer en conflit
avec les 200 clusters globaux.

Résultats mergés dans :
  - data/clustering/card_cluster_full.csv    (écrase les assignations pour ces cartes)
  - data/clustering/cluster_annotations.json (ajoute les nouvelles annotations)

Usage :
    uv run python scripts/cluster_archetype.py --archetype mill
    uv run python scripts/cluster_archetype.py --archetype mill --min-inclusion 3.0
    uv run python scripts/cluster_archetype.py --list
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

EMB_DIR   = ROOT / "data" / "embeddings"
CLUST_DIR = ROOT / "data" / "clustering"

# ── Définition des archétypes ────────────────────────────────────────────────

ARCHETYPES: dict[str, dict] = {
    "mill": {
        "label": "Mill / Opponent Mill",
        "id_base": 1000,
        "commanders": [
            "Phenax, God of Deception",
            "Captain N'ghathrod",
            "Bruvac the Grandiloquent",
            "Wilhelt, the Rotcleaver",
            "The Scarab God",
            "Lazav, Dimir Mastermind",
            "Lazav, the Multifarious",
            "Lazav, Wearer of Faces",
            "Mirko Vosk, Mind Drinker",
            "Mirko, Obsessive Theorist",
            "Umbris, Fear Manifest",
        ],
        "hdbscan": {"min_cluster_size": 8, "min_samples": 5},
        "umap":    {"n_components": 10, "n_neighbors": 15, "min_dist": 0.05},
    },
    "reanimator": {
        "label": "Reanimator / Graveyard",
        "id_base": 1100,
        "commanders": [
            "Meren of Clan Nel Toth",
            "Karador, Ghost Chieftain",
            "Alesha, Who Smiles at Death",
            "Chainer, Dementia Master",
            "Syr Konrad, the Grim",
            "Araumi of the Dead Tide",
            "Varina, Lich Queen",
        ],
        "hdbscan": {"min_cluster_size": 8, "min_samples": 5},
        "umap":    {"n_components": 10, "n_neighbors": 15, "min_dist": 0.05},
    },
    "tokens": {
        "label": "Tokens / Go Wide",
        "id_base": 1200,
        "commanders": [
            "Rhys the Redeemed",
            "Teysa Karlov",
            "Adeline, Resplendent Cathar",
            "Jetmir, Nexus of Revels",
            "Jinnie Fay, Jetmir's Second",
            "Myrel, Shield of Argive",
        ],
        "hdbscan": {"min_cluster_size": 8, "min_samples": 5},
        "umap":    {"n_components": 10, "n_neighbors": 15, "min_dist": 0.05},
    },
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_embeddings() -> tuple[np.ndarray, dict[str, int]]:
    matrix   = np.load(EMB_DIR / "card_embeddings.npy").astype(np.float32)
    card_idx = json.loads((EMB_DIR / "card_index.json").read_text("utf-8"))
    return matrix, card_idx


def fetch_archetype_cards(
    commanders: list[str],
    min_inclusion: float,
) -> list[str]:
    """Retourne les cartes utilisées par les commandants de l'archétype (via DB)."""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.manamind.db.engine import SessionLocal
    from sqlalchemy import text

    placeholders = ", ".join(f":c{i}" for i in range(len(commanders)))
    query = text(f"""
        SELECT DISTINCT card_name
        FROM deck_stat_commander
        WHERE commander IN ({placeholders})
          AND inclusion_rate >= :min_inc
        ORDER BY card_name
    """)
    params = {f"c{i}": c for i, c in enumerate(commanders)}
    params["min_inc"] = min_inclusion

    with SessionLocal() as s:
        rows = s.execute(query, params).fetchall()

    cards = [r.card_name for r in rows]
    log.info("  %d cartes distinctes (inclusion_rate >= %.1f%%)", len(cards), min_inclusion)
    return cards


def run_umap(matrix: np.ndarray, umap_params: dict) -> np.ndarray:
    from umap import UMAP
    reducer = UMAP(
        n_components=umap_params["n_components"],
        n_neighbors=umap_params["n_neighbors"],
        min_dist=umap_params["min_dist"],
        metric="cosine",
        random_state=42,
        verbose=False,
    )
    return reducer.fit_transform(matrix)


def run_hdbscan(reduced: np.ndarray, hdbscan_params: dict) -> np.ndarray:
    import hdbscan
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=hdbscan_params["min_cluster_size"],
        min_samples=hdbscan_params["min_samples"],
        metric="euclidean",
        cluster_selection_method="eom",
    )
    return clusterer.fit_predict(reduced)


def auto_annotate(
    cluster_id_global: int,
    card_names: list[str],
    archetype_label: str,
) -> dict:
    """Génère une annotation automatique pour un cluster d'archétype."""
    top5 = card_names[:5]
    top3_str = ", ".join(top5[:3])

    # Heuristiques simples sur les noms de cartes
    cards_lower = " ".join(top5).lower()
    if any(kw in cards_lower for kw in ["mill", "traumatize", "glimpse", "maddening", "archive trap", "mind funeral"]):
        strategy = "Mill"
        name = f"Mill Package ({top5[0][:20]})"
    elif any(kw in cards_lower for kw in ["horror", "aberration", "nemesis", "alchemist"]):
        strategy = "Tribal Horror"
        name = f"Horror Tribal Package"
    elif any(kw in cards_lower for kw in ["zombie", "wilhelt", "undead", "gravecrawler"]):
        strategy = "Tribal Zombie"
        name = f"Zombie Tribal Package"
    elif any(kw in cards_lower for kw in ["reanimate", "animate dead", "dread return", "exhume"]):
        strategy = "Reanimator"
        name = f"Reanimator Package ({top5[0][:20]})"
    elif any(kw in cards_lower for kw in ["counterspell", "negate", "swan song", "force of will"]):
        strategy = "Control"
        name = f"Counter Package ({top5[0][:20]})"
    elif any(kw in cards_lower for kw in ["dark ritual", "cabal ritual", "cabal coffers", "urborg"]):
        strategy = "Mana"
        name = f"Black Mana Package"
    else:
        strategy = archetype_label
        name = f"{top5[0][:25]} Package" if top5 else f"Archetype Cluster {cluster_id_global}"

    return {
        "cluster_id":          cluster_id_global,
        "name":                name,
        "confidence":          0.65,
        "primary_strategy":    strategy,
        "mechanics":           [],
        "dominant_colors":     [],
        "dominant_types":      [],
        "tribe":               None,
        "description":         (
            f"Cluster d'archétype '{archetype_label}' (ID {cluster_id_global}). "
            f"Cartes représentatives : {top3_str}. Stratégie : {strategy}."
        ),
        "representative_cards": top5,
        "cluster_size":        len(card_names),
        "avg_frequency":       0.0,
        "avg_idf":             0.0,
        "auto_generated":      True,
        "archetype_source":    archetype_label,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Re-clustering ciblé par archétype")
    parser.add_argument("--archetype",     default="mill", help="Archétype à clustériser")
    parser.add_argument("--min-inclusion", type=float, default=2.0,
                        help="Taux d'inclusion minimum (%%) pour inclure une carte (défaut: 2.0)")
    parser.add_argument("--list", action="store_true", help="Lister les archétypes disponibles")
    args = parser.parse_args()

    if args.list:
        print("Archétypes disponibles :")
        for key, cfg in ARCHETYPES.items():
            cmds = ", ".join(cfg["commanders"][:3])
            print(f"  {key:<15} — {cfg['label']} ({len(cfg['commanders'])} commandants : {cmds}...)")
        return

    if args.archetype not in ARCHETYPES:
        log.error("Archétype '%s' inconnu. Disponibles : %s", args.archetype, list(ARCHETYPES.keys()))
        sys.exit(1)

    cfg = ARCHETYPES[args.archetype]
    log.info("=== cluster_archetype.py — archétype '%s' ===", args.archetype)
    log.info("Label : %s", cfg["label"])
    log.info("Commandants : %s", cfg["commanders"])

    # 1. Charger embeddings globaux
    log.info("Chargement embeddings globaux...")
    matrix, card_idx = load_embeddings()
    log.info("  Vocabulaire : %d cartes × %d dims", *matrix.shape)

    # 2. Récupérer les cartes de l'archétype
    log.info("Récupération des cartes de l'archétype (DB)...")
    arch_cards = fetch_archetype_cards(cfg["commanders"], args.min_inclusion)

    # Filtrer celles qui ont un embedding
    arch_cards_emb = [c for c in arch_cards if c in card_idx]
    skipped = len(arch_cards) - len(arch_cards_emb)
    if skipped:
        log.warning("  %d cartes sans embedding ignorées", skipped)
    log.info("  %d cartes avec embedding", len(arch_cards_emb))

    if len(arch_cards_emb) < 30:
        log.error("Trop peu de cartes (%d) pour clustériser. Baisse --min-inclusion.", len(arch_cards_emb))
        sys.exit(1)

    # 3. Extraire la sous-matrice
    indices = [card_idx[c] for c in arch_cards_emb]
    sub_matrix = matrix[indices]
    # L2-normaliser
    norms = np.linalg.norm(sub_matrix, axis=1, keepdims=True)
    sub_matrix = sub_matrix / (norms + 1e-9)
    log.info("  Sous-matrice : %s", sub_matrix.shape)

    # 4. UMAP
    log.info("UMAP %dD (n_neighbors=%d)...", cfg["umap"]["n_components"], cfg["umap"]["n_neighbors"])
    reduced = run_umap(sub_matrix, cfg["umap"])
    log.info("  Réduction terminée : %s", reduced.shape)

    # 5. HDBSCAN
    log.info("HDBSCAN (min_cluster_size=%d, min_samples=%d)...",
             cfg["hdbscan"]["min_cluster_size"], cfg["hdbscan"]["min_samples"])
    labels = run_hdbscan(reduced, cfg["hdbscan"])

    unique_labels = sorted(set(labels))
    n_clusters = len([l for l in unique_labels if l >= 0])
    n_noise    = int((labels == -1).sum())
    log.info("  %d clusters trouvés, %d cartes bruit (%.1f%%)",
             n_clusters, n_noise, 100 * n_noise / len(labels))

    if n_clusters == 0:
        log.error("Aucun cluster trouvé. Essaie de baisser min_cluster_size ou min_samples.")
        sys.exit(1)

    # 6. Mapper vers des IDs globaux (>= id_base)
    id_base  = cfg["id_base"]
    local_to_global = {
        int(local): id_base + int(local)
        for local in unique_labels if local >= 0
    }
    # Pour le bruit : garder l'assignation globale existante (pas d'écrasement)

    # 7. Construire card → global_cluster_id pour les cartes non-bruit
    new_assignments: dict[str, int] = {}
    for card, local_label in zip(arch_cards_emb, labels):
        if int(local_label) >= 0:
            new_assignments[card] = local_to_global[int(local_label)]

    log.info("  %d cartes assignées à un cluster d'archétype", len(new_assignments))

    # 8. Générer les annotations pour les nouveaux clusters
    log.info("Génération des annotations...")
    cluster_to_cards: dict[int, list[str]] = {}
    for card, gid in new_assignments.items():
        cluster_to_cards.setdefault(gid, []).append(card)

    # Charger global_frequency pour trier les cartes représentatives
    full_df = pd.read_csv(CLUST_DIR / "card_cluster_full.csv", encoding="utf-8")
    freq_map = dict(zip(full_df["card_name"], full_df["global_frequency"]))

    new_annotations: list[dict] = []
    for gid in sorted(cluster_to_cards):
        cards_sorted = sorted(
            cluster_to_cards[gid],
            key=lambda c: freq_map.get(c, 0.0),
            reverse=True,
        )
        ann = auto_annotate(gid, cards_sorted, cfg["label"])
        new_annotations.append(ann)
        log.info("  Cluster %d (%d cartes) -> '%s' [%s]",
                 gid, len(cards_sorted), ann["name"], ann["primary_strategy"])

    # 9. Mettre à jour card_cluster_full.csv
    log.info("Mise à jour card_cluster_full.csv...")
    full_df = pd.read_csv(CLUST_DIR / "card_cluster_full.csv", encoding="utf-8")

    overwritten = 0
    for i, row in full_df.iterrows():
        name = row["card_name"]
        if name in new_assignments:
            full_df.at[i, "cluster_id"]        = new_assignments[name]
            full_df.at[i, "is_noise_fallback"]  = False
            overwritten += 1

    full_df.to_csv(CLUST_DIR / "card_cluster_full.csv", index=False, encoding="utf-8")
    log.info("  %d cartes réassignées dans card_cluster_full.csv", overwritten)

    # 10. Mettre à jour cluster_annotations.json
    log.info("Mise à jour cluster_annotations.json...")
    existing_ann = json.loads((CLUST_DIR / "cluster_annotations.json").read_text("utf-8"))
    existing_ids = {a["cluster_id"] for a in existing_ann}

    added = 0
    for ann in new_annotations:
        if ann["cluster_id"] not in existing_ids:
            existing_ann.append(ann)
            added += 1
        else:
            # Mettre à jour si déjà présent (re-run)
            existing_ann = [a if a["cluster_id"] != ann["cluster_id"] else ann for a in existing_ann]

    existing_ann.sort(key=lambda a: a["cluster_id"])
    (CLUST_DIR / "cluster_annotations.json").write_text(
        json.dumps(existing_ann, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("  %d nouvelles annotations ajoutées (%d total)", added, len(existing_ann))

    log.info("=== Terminé — archétype '%s' : %d clusters créés ===", args.archetype, n_clusters)
    log.info("Redémarre le serveur pour prendre en compte les changements.")


if __name__ == "__main__":
    main()
