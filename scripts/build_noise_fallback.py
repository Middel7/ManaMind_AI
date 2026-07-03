#!/usr/bin/env python3
"""
build_noise_fallback.py

Génère card_cluster_full.csv en assignant les cartes bruit HDBSCAN
au centroïde le plus proche par similarité cosine.

Ne relance pas cluster_cards.py entier — utilise les fichiers déjà produits :
  - data/embeddings/card_embeddings.npy  (embeddings Card2Vec)
  - data/embeddings/card_index.json      (nom → index)
  - data/clustering/clusters/            (assignations HDBSCAN)
  - data/clustering/cluster_centroids.csv (centroides par cluster)

Sortie :
  - data/clustering/card_cluster_full.csv
      card_name, global_frequency, cluster_id, is_noise_fallback

Usage :
    uv run python scripts/build_noise_fallback.py
"""
from __future__ import annotations

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
STATS_DIR = ROOT / "data" / "stats"


def main() -> None:
    log.info("=== build_noise_fallback.py ===")

    # 1. Charger les embeddings Card2Vec
    log.info("Chargement embeddings...")
    matrix = np.load(EMB_DIR / "card_embeddings.npy").astype(np.float32)
    card_idx: dict[str, int] = json.loads(
        (EMB_DIR / "card_index.json").read_text(encoding="utf-8")
    )
    idx_to_name = {v: k for k, v in card_idx.items()}
    all_names = [idx_to_name[i] for i in range(len(idx_to_name))]
    log.info("  %d cartes dans le vocabulaire Card2Vec", len(all_names))

    # 2. Charger les assignations HDBSCAN (CSV individuels par cluster)
    log.info("Chargement des assignations HDBSCAN...")
    assigned: dict[str, int] = {}
    for path in sorted((CLUST_DIR / "clusters").glob("cluster_*.csv")):
        df = pd.read_csv(path, encoding="utf-8")
        for _, row in df.iterrows():
            assigned[row["card_name"]] = int(row["cluster_id"])
    log.info("  %d cartes dans des clusters HDBSCAN", len(assigned))

    noise_names = [n for n in all_names if n not in assigned]
    log.info("  %d cartes bruit (non assignées)", len(noise_names))

    # 3. Charger les centroides
    log.info("Chargement des centroides...")
    centroids_df = pd.read_csv(CLUST_DIR / "cluster_centroids.csv", encoding="utf-8")
    clust_ids = centroids_df["cluster_id"].astype(int).tolist()

    emb_cols = [c for c in centroids_df.columns if c.startswith("emb_")]
    if not emb_cols:
        # Les centroides sont stockés comme vecteurs moyens des cartes dans chaque cluster
        log.info("  Colonnes emb_ absentes — recalcul des centroides depuis les embeddings")
        centroids = np.zeros((len(clust_ids), matrix.shape[1]), dtype=np.float32)
        for i, cid in enumerate(clust_ids):
            members = [card_idx[n] for n in assigned if assigned[n] == cid and n in card_idx]
            if members:
                centroids[i] = matrix[members].mean(axis=0)
    else:
        centroids = centroids_df[emb_cols].to_numpy(dtype=np.float32)

    log.info("  %d centroides chargés", len(clust_ids))

    # 4. Normaliser pour cosine similarity = dot product
    cent_norm = centroids / (np.linalg.norm(centroids, axis=1, keepdims=True) + 1e-9)

    # 5. Assigner les cartes bruit au centroïde le plus proche
    log.info("Assignation des cartes bruit par cosine (batch 2000)...")
    noise_indices = [card_idx[n] for n in noise_names if n in card_idx]
    noise_names_filtered = [n for n in noise_names if n in card_idx]
    skipped = len(noise_names) - len(noise_names_filtered)
    if skipped:
        log.warning("  %d cartes bruit absentes de card_index — ignorées", skipped)

    fallback_cluster: dict[str, int] = {}
    BATCH = 2000
    for start in range(0, len(noise_indices), BATCH):
        batch_idx = noise_indices[start: start + BATCH]
        batch_names = noise_names_filtered[start: start + BATCH]
        vecs = matrix[batch_idx]
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs_norm = vecs / (norms + 1e-9)
        sims = vecs_norm @ cent_norm.T          # (batch, n_clusters)
        best = np.argmax(sims, axis=1)
        for name, b in zip(batch_names, best):
            fallback_cluster[name] = clust_ids[b]
        if (start // BATCH) % 5 == 0:
            log.info("  ... %d / %d", min(start + BATCH, len(noise_indices)), len(noise_indices))

    log.info("  %d cartes bruit assignées par fallback", len(fallback_cluster))

    # 6. Charger global_frequency
    log.info("Chargement global_frequency...")
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        from src.manamind.db.engine import SessionLocal
        from sqlalchemy import text
        with SessionLocal() as s:
            rows = s.execute(text(
                "SELECT card_name, global_frequency FROM deck_stat_global"
            )).fetchall()
        freq_map = {r.card_name: float(r.global_frequency) for r in rows}
        log.info("  %d cartes avec global_frequency (DB)", len(freq_map))
    except Exception as e:
        log.warning("  DB indisponible (%s) — fallback CSV", e)
        tfidf = pd.read_csv(STATS_DIR / "commander_tfidf.csv", encoding="utf-8")
        freq_map = tfidf.groupby("card_name")["inclusion_rate"].mean().to_dict()

    # 7. Construire le DataFrame final
    rows_out = []
    for name in all_names:
        if name in assigned:
            cid = assigned[name]
            is_fallback = False
        elif name in fallback_cluster:
            cid = fallback_cluster[name]
            is_fallback = True
        else:
            continue  # ne devrait pas arriver
        rows_out.append({
            "card_name":         name,
            "global_frequency":  round(freq_map.get(name, 0.0), 4),
            "cluster_id":        cid,
            "is_noise_fallback": is_fallback,
        })

    out_df = pd.DataFrame(rows_out).sort_values("global_frequency", ascending=False)
    out_path = CLUST_DIR / "card_cluster_full.csv"
    out_df.to_csv(out_path, index=False, encoding="utf-8")

    n_hdbscan  = int((~out_df["is_noise_fallback"]).sum())
    n_fallback = int(out_df["is_noise_fallback"].sum())
    log.info("Écrit : card_cluster_full.csv")
    log.info("  Total     : %d cartes", len(out_df))
    log.info("  HDBSCAN   : %d (%.1f%%)", n_hdbscan,  100 * n_hdbscan  / len(out_df))
    log.info("  Fallback  : %d (%.1f%%)", n_fallback, 100 * n_fallback / len(out_df))
    log.info("=== Terminé ===")


if __name__ == "__main__":
    main()
