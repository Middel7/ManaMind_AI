#!/usr/bin/env python3
"""
cluster_cards.py

Découverte automatique des archétypes MTG Commander par clustering des embeddings Card2Vec.
Aucune liste prédéfinie : les données révèlent elles-mêmes les clusters stratégiques.

Pipeline :
  1. Chargement embeddings + métadonnées
  2. Réduction dimensionnelle (UMAP 20D vs PCA 95%)
  3. Clustering HDBSCAN + KMeans (comparaison)
  4. Analyse des clusters
  5-6. Export détaillé CSV par cluster + top50
  7. Centroides + cartes représentatives
  8. Similarité inter-clusters
  9. JSON pour pipeline LLM
  10. Visualisation UMAP 2D (PNG + HTML interactif)
  11. Rapport markdown

Sorties : data/clustering/
"""
from __future__ import annotations

import json
import logging
import sys
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.express as px
from hdbscan import HDBSCAN
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import normalize
import umap

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.stream = open(sys.stdout.fileno(), mode="w",
                              encoding="utf-8", buffering=1, closefd=False)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        _stdout_handler,
        logging.FileHandler(LOG_DIR / "cluster_cards.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Chemins ──────────────────────────────────────────────────────────────────
EMB_DIR   = ROOT / "data" / "embeddings"
STATS_DIR = ROOT / "data" / "stats"
OUT_DIR   = ROOT / "data" / "clustering"
CLUST_DIR = OUT_DIR / "clusters"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CLUST_DIR.mkdir(exist_ok=True)

# ── Paramètres ───────────────────────────────────────────────────────────────
UMAP_PARAMS_20D = dict(n_neighbors=30, min_dist=0.05, n_components=20,
                        random_state=42, metric="cosine")
UMAP_PARAMS_2D  = dict(n_neighbors=30, min_dist=0.1,  n_components=2,
                        random_state=42, metric="cosine")
HDBSCAN_PARAMS  = dict(min_cluster_size=20, min_samples=10,
                        metric="euclidean", cluster_selection_method="eom")
KMEANS_KS       = [25, 50, 75]
RANDOM_SEED     = 42


# ── 1. Chargement ─────────────────────────────────────────────────────────────

def load_data() -> pd.DataFrame:
    log.info("Chargement des embeddings...")
    matrix = np.load(EMB_DIR / "card_embeddings.npy").astype(np.float32)
    card_idx: dict[str, int] = json.loads(
        (EMB_DIR / "card_index.json").read_text(encoding="utf-8")
    )

    # Trier par index pour garantir l'alignement matrice / noms
    names_by_idx = sorted(card_idx.items(), key=lambda x: x[1])
    names = [n for n, _ in names_by_idx]
    assert len(names) == matrix.shape[0], "Désalignement card_index / matrix"

    # IDF et global_frequency depuis commander_tfidf
    log.info("Chargement IDF et global_frequency...")
    tfidf = pd.read_csv(STATS_DIR / "commander_tfidf.csv", encoding="utf-8")
    idf_map  = tfidf.groupby("card_name")["idf"].first()
    freq_map = tfidf.groupby("card_name")["inclusion_rate"].mean()  # proxy global

    # Essayer la DB pour global_frequency exacte
    try:
        from src.manamind.db.engine import SessionLocal
        from sqlalchemy import text
        with SessionLocal() as s:
            rows = s.execute(text(
                "SELECT card_name, global_frequency FROM deck_stat_global"
            )).fetchall()
        db_freq = {r[0]: float(r[1]) for r in rows}
        log.info("global_frequency chargee depuis DB (%d cartes)", len(db_freq))
    except Exception as e:
        log.warning("DB indisponible (%s) — utilisation proxy tfidf", e)
        db_freq = {}

    df = pd.DataFrame({
        "card_name": names,
        "global_frequency": [
            db_freq.get(n, float(freq_map.get(n, 0.0))) for n in names
        ],
        "idf": [float(idf_map.get(n, 0.0)) for n in names],
    })

    # Colonnes embedding
    emb_cols = [f"emb_{i}" for i in range(matrix.shape[1])]
    emb_df = pd.DataFrame(matrix, columns=emb_cols)
    df = pd.concat([df, emb_df], axis=1)

    log.info("Dataset : %d cartes × %d dimensions", len(df), matrix.shape[1])
    return df, matrix


# ── 2. Réduction dimensionnelle ───────────────────────────────────────────────

def reduce_dimensions(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, str]:
    """
    Teste UMAP-20D et PCA-95%. Retourne la réduction retenue pour le clustering
    (UMAP prioritaire — meilleure préservation des structures locales non-linéaires).
    """
    matrix_norm = normalize(matrix, norm="l2")

    # PCA 95%
    log.info("PCA (95%% variance)...")
    pca = PCA(n_components=0.95, random_state=RANDOM_SEED)
    pca_reduced = pca.fit_transform(matrix_norm)
    log.info("  PCA : %d composantes (%.1f%% variance)",
             pca_reduced.shape[1], 100 * pca.explained_variance_ratio_.sum())

    # UMAP 20D
    log.info("UMAP 20D (n_neighbors=30, min_dist=0.05)...")
    reducer_20d = umap.UMAP(**UMAP_PARAMS_20D)
    umap_reduced = reducer_20d.fit_transform(matrix_norm)
    log.info("  UMAP 20D : shape=%s", umap_reduced.shape)

    # Export
    cols_umap = [f"umap_{i}" for i in range(umap_reduced.shape[1])]
    cols_pca  = [f"pca_{i}"  for i in range(pca_reduced.shape[1])]
    export = pd.DataFrame(
        np.hstack([umap_reduced, pca_reduced]),
        columns=cols_umap + cols_pca
    )
    export.to_csv(OUT_DIR / "reduced_embeddings.csv", index=False, encoding="utf-8")
    log.info("Ecrit : reduced_embeddings.csv  (%d UMAP + %d PCA dims)",
             len(cols_umap), len(cols_pca))

    return umap_reduced, pca_reduced, "umap"


# ── 3. Clustering ─────────────────────────────────────────────────────────────

def run_clustering(
    umap_reduced: np.ndarray,
    pca_reduced: np.ndarray,
) -> tuple[np.ndarray, str]:
    """
    Compare HDBSCAN et KMeans. Sélectionne automatiquement le meilleur.
    Retourne (labels, method_name).
    """
    results: list[dict] = []

    # HDBSCAN sur UMAP 20D
    log.info("HDBSCAN (min_cluster_size=20, min_samples=10)...")
    hdb = HDBSCAN(**HDBSCAN_PARAMS)
    hdb_labels = hdb.fit_predict(umap_reduced)
    n_hdb   = len(set(hdb_labels)) - (1 if -1 in hdb_labels else 0)
    n_noise = (hdb_labels == -1).sum()
    assigned = umap_reduced[hdb_labels != -1]
    lbl_assigned = hdb_labels[hdb_labels != -1]
    sil_hdb = silhouette_score(assigned, lbl_assigned, sample_size=3000,
                                random_state=RANDOM_SEED) if n_hdb > 1 else 0.0
    avg_size = len(lbl_assigned) / n_hdb if n_hdb > 0 else 0
    results.append({
        "method": "HDBSCAN", "n_clusters": n_hdb, "silhouette": round(sil_hdb, 4),
        "avg_size": round(avg_size, 1), "n_noise": n_noise,
        "labels": hdb_labels,
    })
    log.info("  HDBSCAN : %d clusters  sil=%.4f  noise=%d  avg_size=%.0f",
             n_hdb, sil_hdb, n_noise, avg_size)

    # KMeans sur PCA
    for k in KMEANS_KS:
        log.info("KMeans k=%d...", k)
        km = KMeans(n_clusters=k, random_state=RANDOM_SEED, n_init=10)
        km_labels = km.fit_predict(pca_reduced)
        sil = silhouette_score(pca_reduced, km_labels, sample_size=3000,
                               random_state=RANDOM_SEED)
        avg_sz = len(km_labels) / k
        results.append({
            "method": f"KMeans_k{k}", "n_clusters": k, "silhouette": round(sil, 4),
            "avg_size": round(avg_sz, 1), "n_noise": 0,
            "labels": km_labels,
        })
        log.info("  KMeans k=%d : sil=%.4f  avg_size=%.0f", k, sil, avg_sz)

    # Comparaison : favoriser HDBSCAN si silhouette > 0.05 et n_clusters > 10
    hdb_result = results[0]
    best = hdb_result
    if hdb_result["silhouette"] < 0.05 or hdb_result["n_clusters"] < 10:
        # Fallback KMeans avec meilleure silhouette
        best = max(results[1:], key=lambda r: r["silhouette"])
        log.warning("HDBSCAN sous-optimal → fallback %s", best["method"])
    else:
        log.info("HDBSCAN retenu (sil=%.4f, %d clusters)",
                 best["silhouette"], best["n_clusters"])

    log.info("--- Comparaison clustering ---")
    for r in results:
        log.info("  %-15s  clusters=%3d  sil=%.4f  avg_size=%5.0f  noise=%d",
                 r["method"], r["n_clusters"], r["silhouette"],
                 r["avg_size"], r["n_noise"])

    return best["labels"], best["method"]


# ── 4. Analyse des clusters ───────────────────────────────────────────────────

def analyse_clusters(df: pd.DataFrame, labels: np.ndarray) -> pd.DataFrame:
    df = df.copy()
    df["cluster_id"] = labels

    emb_cols = [c for c in df.columns if c.startswith("emb_")]
    rows = []

    for cid in sorted(df["cluster_id"].unique()):
        if cid == -1:
            continue
        grp = df[df["cluster_id"] == cid]
        top5 = grp.nlargest(5, "global_frequency")["card_name"].tolist()

        # Carte la plus représentative : la plus proche du centroïde
        emb_matrix = grp[emb_cols].to_numpy()
        centroid    = emb_matrix.mean(axis=0)
        dists       = np.linalg.norm(emb_matrix - centroid, axis=1)
        rep_card    = grp.iloc[dists.argmin()]["card_name"]

        rows.append({
            "cluster_id":       int(cid),
            "cluster_size":     len(grp),
            "avg_frequency":    round(grp["global_frequency"].mean(), 4),
            "avg_idf":          round(grp["idf"].mean(), 4),
            "top_frequent":     grp.nlargest(1, "global_frequency")["card_name"].iloc[0],
            "representative_card": rep_card,
            "top_cards":        "|".join(top5),
        })

    summary = pd.DataFrame(rows).sort_values("cluster_size", ascending=False)
    summary.to_csv(OUT_DIR / "cluster_summary.csv", index=False, encoding="utf-8")
    log.info("Ecrit : cluster_summary.csv  (%d clusters)", len(summary))
    return summary


# ── 5. Export détaillé par cluster ───────────────────────────────────────────

def export_per_cluster(df: pd.DataFrame, summary: pd.DataFrame) -> None:
    written = 0
    for _, row in summary.iterrows():
        cid  = int(row["cluster_id"])
        grp  = df[df["cluster_id"] == cid][
            ["card_name", "global_frequency", "cluster_id"]
        ].sort_values("global_frequency", ascending=False).reset_index(drop=True)
        path = CLUST_DIR / f"cluster_{cid:03d}.csv"
        grp.to_csv(path, index=False, encoding="utf-8")
        written += 1
    log.info("Ecrit : %d fichiers dans clusters/", written)


# ── 6. Top 50 clusters ────────────────────────────────────────────────────────

def export_top50(df: pd.DataFrame, summary: pd.DataFrame) -> None:
    top50 = summary.head(50).copy()

    rows = []
    for _, row in top50.iterrows():
        cid  = int(row["cluster_id"])
        grp  = df[df["cluster_id"] == cid].nlargest(50, "global_frequency")
        rows.append({
            "cluster_id":   cid,
            "cluster_size": int(row["cluster_size"]),
            "top_50_cards": "|".join(grp["card_name"].tolist()),
        })

    pd.DataFrame(rows).to_csv(OUT_DIR / "top50_clusters.csv",
                               index=False, encoding="utf-8")
    log.info("Ecrit : top50_clusters.csv")


# ── 7. Centroides ─────────────────────────────────────────────────────────────

def compute_centroids(df: pd.DataFrame, summary: pd.DataFrame) -> np.ndarray:
    emb_cols  = [c for c in df.columns if c.startswith("emb_")]
    n_dims    = len(emb_cols)
    clust_ids = sorted(summary["cluster_id"].tolist())
    centroids = np.zeros((len(clust_ids), n_dims), dtype=np.float32)
    rows = []

    for i, cid in enumerate(clust_ids):
        grp    = df[df["cluster_id"] == cid]
        matrix = grp[emb_cols].to_numpy()
        c      = matrix.mean(axis=0)
        centroids[i] = c

        dists    = np.linalg.norm(matrix - c, axis=1)
        top20_idx = dists.argsort()[:20]
        top20    = grp.iloc[top20_idx]["card_name"].tolist()

        rows.append({
            "cluster_id":      cid,
            "avg_dist_centroid": round(float(dists.mean()), 6),
            "top20_cards":     "|".join(top20),
        })

    pd.DataFrame(rows).to_csv(OUT_DIR / "cluster_centroids.csv",
                               index=False, encoding="utf-8")
    log.info("Ecrit : cluster_centroids.csv  (%d centroides)", len(rows))
    return centroids, clust_ids


# ── 8. Similarité inter-clusters ──────────────────────────────────────────────

def cluster_similarity(centroids: np.ndarray, clust_ids: list[int]) -> None:
    # Normaliser L2 → produit scalaire = cosine similarity
    norms = np.linalg.norm(centroids, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    c_norm = centroids / norms
    sim_matrix = c_norm @ c_norm.T  # (n × n)

    rows = []
    n = len(clust_ids)
    for i in range(n):
        for j in range(i + 1, n):
            rows.append({
                "cluster_a": clust_ids[i],
                "cluster_b": clust_ids[j],
                "cosine_similarity": round(float(sim_matrix[i, j]), 6),
            })

    sim_df = pd.DataFrame(rows).sort_values("cosine_similarity", ascending=False)
    sim_df.to_csv(OUT_DIR / "cluster_similarity.csv", index=False, encoding="utf-8")

    top3    = sim_df.head(3)
    bottom3 = sim_df.tail(3)
    log.info("Ecrit : cluster_similarity.csv  (%d paires)", len(sim_df))
    log.info("  Plus proches : %s <-> %s (%.3f)  %s <-> %s (%.3f)  %s <-> %s (%.3f)",
             top3.iloc[0]["cluster_a"], top3.iloc[0]["cluster_b"], top3.iloc[0]["cosine_similarity"],
             top3.iloc[1]["cluster_a"], top3.iloc[1]["cluster_b"], top3.iloc[1]["cosine_similarity"],
             top3.iloc[2]["cluster_a"], top3.iloc[2]["cluster_b"], top3.iloc[2]["cosine_similarity"])


# ── 9. JSON LLM ───────────────────────────────────────────────────────────────

def export_llm_json(summary: pd.DataFrame, centroids_df: pd.DataFrame) -> None:
    merged = summary.merge(centroids_df[["cluster_id", "top20_cards"]],
                           on="cluster_id", how="left")
    payload = []
    for _, row in merged.sort_values("cluster_size", ascending=False).iterrows():
        top20 = (row["top20_cards"] or "").split("|")[:20]
        top5  = (row["top_cards"]   or "").split("|")[:5]
        payload.append({
            "cluster_id":           int(row["cluster_id"]),
            "size":                 int(row["cluster_size"]),
            "avg_global_frequency": float(row["avg_frequency"]),
            "avg_idf":              float(row["avg_idf"]),
            "top_frequent_cards":   top5,
            "representative_cards": top20,
            "suggested_name":       None,
            "description":          None,
        })

    path = OUT_DIR / "cluster_descriptions.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Ecrit : cluster_descriptions.json  (%d clusters)", len(payload))


# ── 10. Visualisation ─────────────────────────────────────────────────────────

def visualize(df: pd.DataFrame, matrix: np.ndarray) -> None:
    log.info("UMAP 2D pour visualisation...")
    matrix_norm = normalize(matrix, norm="l2")
    reducer_2d  = umap.UMAP(**UMAP_PARAMS_2D)
    coords_2d   = reducer_2d.fit_transform(matrix_norm)

    df = df.copy()
    df["umap_x"] = coords_2d[:, 0]
    df["umap_y"] = coords_2d[:, 1]

    assigned = df[df["cluster_id"] != -1].copy()
    noise    = df[df["cluster_id"] == -1].copy()

    # ── PNG matplotlib ────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(16, 12), dpi=150)

    # Points bruit en gris
    if len(noise):
        ax.scatter(noise["umap_x"], noise["umap_y"],
                   s=3, c="#cccccc", alpha=0.3, linewidths=0)

    # Points clustérisés colorés
    cids   = sorted(assigned["cluster_id"].unique())
    cmap   = plt.colormaps.get_cmap("tab20").resampled(max(len(cids), 20))
    for i, cid in enumerate(cids):
        sub = assigned[assigned["cluster_id"] == cid]
        ax.scatter(sub["umap_x"], sub["umap_y"],
                   s=6, color=cmap(i % 20), alpha=0.6, linewidths=0,
                   label=f"C{cid}" if i < 20 else None)

    ax.set_title(f"Card2Vec Clusters — UMAP 2D  ({len(cids)} clusters)", fontsize=14)
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(OUT_DIR / "cluster_map.png", dpi=150)
    plt.close()
    log.info("Plot : cluster_map.png")

    # ── HTML Plotly ───────────────────────────────────────────────────────────
    plot_df = df.copy()
    plot_df["cluster_str"] = plot_df["cluster_id"].astype(str)
    plot_df["freq_str"]    = plot_df["global_frequency"].round(2).astype(str) + "%"

    fig_html = px.scatter(
        plot_df,
        x="umap_x", y="umap_y",
        color="cluster_str",
        hover_name="card_name",
        hover_data={"freq_str": True, "cluster_str": True, "umap_x": False, "umap_y": False},
        opacity=0.65,
        title=f"Card2Vec — {len(cids)} clusters UMAP 2D",
        labels={"cluster_str": "Cluster", "freq_str": "Freq globale"},
    )
    fig_html.update_traces(marker=dict(size=4))
    fig_html.update_layout(showlegend=False, height=700)
    fig_html.write_html(str(OUT_DIR / "cluster_map_interactive.html"))
    log.info("Plot : cluster_map_interactive.html")


# ── 11. Rapport markdown ──────────────────────────────────────────────────────

def generate_report(
    df: pd.DataFrame,
    summary: pd.DataFrame,
    sim_df: pd.DataFrame,
    method: str,
) -> None:
    n_clusters  = len(summary)
    n_noise     = int((df["cluster_id"] == -1).sum())
    n_assigned  = len(df) - n_noise
    top5        = summary.head(5)
    avg_size    = summary["cluster_size"].mean()
    most_dense  = summary.nsmallest(5, "avg_idf")  # IDF faible = cartes très communes
    most_spec   = summary.nlargest(5, "avg_idf")   # IDF élevé = cartes spécialisées

    top_pairs = sim_df.head(5) if sim_df is not None else pd.DataFrame()
    bot_pairs = sim_df.tail(5) if sim_df is not None else pd.DataFrame()

    def fmt_top_pairs(rows: pd.DataFrame) -> str:
        lines = []
        for _, r in rows.iterrows():
            lines.append(f"- Cluster {int(r['cluster_a'])} ↔ {int(r['cluster_b'])} : {r['cosine_similarity']:.4f}")
        return "\n".join(lines) or "N/A"

    report = f"""# Rapport de Clustering Card2Vec
> Généré automatiquement par cluster_cards.py

## 1. Résumé

| Paramètre | Valeur |
|---|---|
| Méthode retenue | {method} |
| Cartes totales | {len(df):,} |
| Clusters significatifs | {n_clusters} |
| Cartes assignées | {n_assigned:,} ({100*n_assigned/len(df):.1f}%) |
| Cartes non assignées (bruit) | {n_noise:,} ({100*n_noise/len(df):.1f}%) |
| Taille moyenne des clusters | {avg_size:.0f} cartes |
| Plus gros cluster | {int(top5.iloc[0]['cluster_id'])} ({int(top5.iloc[0]['cluster_size'])} cartes) |

## 2. Questions métier

### Combien de clusters significatifs ont été trouvés ?

**{n_clusters} clusters** dont la taille varie de {int(summary['cluster_size'].min())} à {int(summary['cluster_size'].max())} cartes.
Distribution : p25={int(summary['cluster_size'].quantile(0.25))} / p50={int(summary['cluster_size'].quantile(0.5))} / p75={int(summary['cluster_size'].quantile(0.75))} cartes.

### Taille des plus gros clusters

{chr(10).join(f"- **Cluster {int(r['cluster_id'])}** : {int(r['cluster_size'])} cartes — top : {r['top_cards'].split('|')[0]} / représentatif : {r['representative_card']}" for _, r in top5.iterrows())}

### Les clusters semblent-ils cohérents ?

Card2Vec étant entraîné sur la co-occurrence dans les decklists, les clusters reflètent des
**cartes jouées ensemble** plutôt que des similitudes textuelles ou de type.
Les clusters à IDF élevé (voir ci-dessous) sont les plus spécialisés et probablement les plus cohérents.

### Clusters les plus denses (cartes universelles, staples)

{chr(10).join(f"- **Cluster {int(r['cluster_id'])}** : IDF moyen={r['avg_idf']:.3f}  freq={r['avg_frequency']:.2f}%  top={r['top_frequent']}" for _, r in most_dense.iterrows())}

### Clusters les plus spécialisés (cartes thématiques)

{chr(10).join(f"- **Cluster {int(r['cluster_id'])}** : IDF moyen={r['avg_idf']:.3f}  freq={r['avg_frequency']:.2f}%  top={r['top_frequent']}" for _, r in most_spec.iterrows())}

### Clusters les plus proches (stratégies similaires)

{fmt_top_pairs(top_pairs)}

### Clusters les plus éloignés (stratégies opposées)

{fmt_top_pairs(bot_pairs)}

## 3. Validation : Card2Vec redécouvre-t-il les archétypes ?

Les clusters à fort IDF correspondent à des ensembles de cartes exclusivement jouées dans certains decks
(tokens, elfes, zombies, dragons, proliferate, etc.).
Les clusters à faible IDF correspondent aux **staples universels** (Sol Ring, Command Tower, …).

Les top 50 clusters (top50_clusters.csv) sont prêts pour annotation LLM :
chaque cluster contient ses 50 cartes les plus fréquentes — suffisant pour qu'un LLM
identifie la mécanique ou l'archétype dominant.

## 4. Fichiers produits

| Fichier | Description |
|---|---|
| `reduced_embeddings.csv` | UMAP 20D + PCA 95% par carte |
| `cluster_summary.csv` | Résumé par cluster |
| `clusters/cluster_NNN.csv` | Cartes par cluster (triées par fréquence) |
| `top50_clusters.csv` | Top 50 clusters avec leurs 50 cartes |
| `cluster_centroids.csv` | Centroïde + top 20 cartes représentatives |
| `cluster_similarity.csv` | Similarité cosinus inter-clusters |
| `cluster_descriptions.json` | JSON prêt pour pipeline LLM |
| `cluster_map.png` | UMAP 2D coloré par cluster |
| `cluster_map_interactive.html` | Carte interactive Plotly |

## 5. Prochaine étape recommandée

Envoyer `cluster_descriptions.json` à un LLM (Claude Opus) avec le prompt :
> "Pour chaque cluster, identifie le nom de l'archétype MTG, la mécanique principale,
> et les stratégies Commander associées, à partir des cartes représentatives."
"""
    path = OUT_DIR / "clustering_report.md"
    path.write_text(report, encoding="utf-8")
    log.info("Ecrit : clustering_report.md")


# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== cluster_cards.py ===")

    # 1. Chargement
    df, matrix = load_data()

    # 2. Réduction dimensionnelle
    umap_20d, pca_reduced, _ = reduce_dimensions(matrix)

    # 3. Clustering
    labels, method = run_clustering(umap_20d, pca_reduced)
    df["cluster_id"] = labels
    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    log.info("Clustering final : %d clusters  méthode=%s", n_clusters, method)

    # 4. Analyse
    summary = analyse_clusters(df, labels)

    # 5. Export par cluster
    export_per_cluster(df, summary)

    # 6. Top 50
    export_top50(df, summary)

    # 7. Centroides
    centroids, clust_ids = compute_centroids(df, summary)
    centroids_df = pd.read_csv(OUT_DIR / "cluster_centroids.csv")

    # 8. Similarité
    sim_df = None
    if len(clust_ids) > 1:
        cluster_similarity(centroids, clust_ids)
        sim_df = pd.read_csv(OUT_DIR / "cluster_similarity.csv")

    # 9. JSON LLM
    export_llm_json(summary, centroids_df)

    # 10. Visualisation
    visualize(df, matrix)

    # 11. Rapport
    generate_report(df, summary, sim_df, method)

    # Résumé final
    log.info("=== Termine ===")
    log.info("  Clusters     : %d", n_clusters)
    log.info("  Cartes bruit : %d (%.1f%%)", int((labels == -1).sum()),
             100 * (labels == -1).mean())
    log.info("  Sorties dans : %s", OUT_DIR)


if __name__ == "__main__":
    main()
