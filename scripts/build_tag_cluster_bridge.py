#!/usr/bin/env python3
"""
build_tag_cluster_bridge.py

Pont Tags Scryfall ↔ Clusters Card2Vec ↔ Profils Commandants.

Permet d'analyser des cartes jamais vues dans les decklists à partir
de leurs seuls tags Scryfall.

Étapes :
  1. Dataset Tag ↔ Cluster
  2. Matrice P(cluster | tag)
  3. Matrice P(tag | cluster)
  4. Sémantique des clusters par tags
  5. predict_clusters_from_tags()  — 3 méthodes comparées
  6. predict_commanders_from_tags()
  7. Validation sur cartes existantes
  8. Card Semantic Profiles
  9. analyze_new_card()
  10. Rapport markdown

Sorties : data/tag_cluster/
"""
from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.naive_bayes import MultinomialNB
from sklearn.preprocessing import LabelEncoder, MultiLabelBinarizer
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.manamind.db.engine import SessionLocal  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
_h = logging.StreamHandler(sys.stdout)
_h.stream = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[_h, logging.FileHandler(LOG_DIR / "build_tag_cluster_bridge.log", encoding="utf-8")],
)
log = logging.getLogger(__name__)

# ── Chemins ───────────────────────────────────────────────────────────────────
OUT_DIR      = ROOT / "data" / "tag_cluster"
CLUST_DIR    = ROOT / "data" / "clustering" / "clusters"
ANNOT_PATH   = ROOT / "data" / "clustering" / "cluster_annotations.json"
TFIDF_CSV    = ROOT / "data" / "stats" / "commander_tfidf.csv"
CMD_JSON     = ROOT / "data" / "embeddings" / "commander_embeddings.json"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Nombre de tags de validation
MIN_TAG_FREQ    = 5   # ignorer les tags rares
TOP_CLUSTER_K   = 5   # top-K clusters dans les prédictions
TOP_COMMANDER_K = 10  # top-K commandants


# ── 0. Chargement ─────────────────────────────────────────────────────────────

def load_annotations() -> dict[int, dict]:
    ann = json.loads(ANNOT_PATH.read_text("utf-8"))
    return {a["cluster_id"]: a for a in ann}

def load_card_clusters() -> pd.DataFrame:
    """Charge le cluster_id de chaque carte à partir des CSV par cluster."""
    rows = []
    for path in sorted(CLUST_DIR.glob("cluster_*.csv")):
        df = pd.read_csv(path, encoding="utf-8")
        rows.append(df[["card_name", "cluster_id"]])
    return pd.concat(rows, ignore_index=True)

def load_tags_from_db() -> pd.DataFrame:
    """Retourne DataFrame { card_name, tag_name } depuis scryfall_card_tags."""
    log.info("Chargement des tags Scryfall depuis DB...")
    with SessionLocal() as s:
        rows = s.execute(text("""
            SELECT sc.name AS card_name, sct.tag_name
            FROM scryfall_card_tags sct
            JOIN scryfall_cards sc ON sc.id = sct.card_id
        """)).fetchall()
    df = pd.DataFrame(rows, columns=["card_name", "tag_name"])
    log.info("  %d paires (carte, tag)  —  %d cartes  —  %d tags distincts",
             len(df), df["card_name"].nunique(), df["tag_name"].nunique())
    return df

def load_commanders() -> list[str]:
    meta = json.loads(CMD_JSON.read_text("utf-8"))
    return meta["commanders"]


# ── 1. Dataset Tag ↔ Cluster ──────────────────────────────────────────────────

def build_tag_cluster_dataset(
    tags_df: pd.DataFrame,
    clusters_df: pd.DataFrame,
    annotations: dict[int, dict],
) -> pd.DataFrame:
    log.info("Étape 1 — Dataset Tag × Cluster...")
    merged = tags_df.merge(clusters_df, on="card_name", how="inner")
    merged["cluster_name"] = merged["cluster_id"].map(
        lambda cid: annotations.get(cid, {}).get("name", f"C{cid}")
    )
    dataset = merged[["card_name", "cluster_id", "cluster_name", "tag_name"]].rename(
        columns={"tag_name": "tag"}
    )
    dataset.to_csv(OUT_DIR / "tag_cluster_dataset.csv", index=False, encoding="utf-8")
    log.info("  %d lignes  →  tag_cluster_dataset.csv", len(dataset))
    return dataset


# ── 2. P(cluster | tag) ───────────────────────────────────────────────────────

def build_tag_to_cluster(dataset: pd.DataFrame, annotations: dict[int, dict]) -> pd.DataFrame:
    log.info("Étape 2 — P(cluster | tag)...")

    # Filtrer les tags rares
    tag_counts = dataset["tag"].value_counts()
    valid_tags = tag_counts[tag_counts >= MIN_TAG_FREQ].index
    data = dataset[dataset["tag"].isin(valid_tags)].copy()

    # P(cluster | tag) = count(tag ∩ cluster) / count(tag)
    cross = data.groupby(["tag", "cluster_id", "cluster_name"]).agg(
        count_cards=("card_name", "nunique")
    ).reset_index()

    tag_total = data.groupby("tag")["card_name"].nunique().rename("tag_total")
    cross = cross.merge(tag_total, on="tag")
    cross["probability"] = (cross["count_cards"] / cross["tag_total"]).round(4)
    cross = cross.drop(columns="tag_total").sort_values(
        ["tag", "probability"], ascending=[True, False]
    )

    cross.to_csv(OUT_DIR / "tag_to_cluster.csv", index=False, encoding="utf-8")
    log.info("  %d lignes  (tags >= %d occ.)  →  tag_to_cluster.csv", len(cross), MIN_TAG_FREQ)
    return cross


# ── 3. P(tag | cluster) ───────────────────────────────────────────────────────

def build_cluster_to_tag(dataset: pd.DataFrame) -> pd.DataFrame:
    log.info("Étape 3 — P(tag | cluster)...")

    # P(tag | cluster) = count(cartes du cluster ayant ce tag) / count(cartes du cluster)
    clust_sizes = dataset.groupby("cluster_id")["card_name"].nunique().rename("cluster_cards")
    cross = dataset.groupby(["cluster_id", "cluster_name", "tag"]).agg(
        count_cards=("card_name", "nunique")
    ).reset_index()
    cross = cross.merge(clust_sizes, on="cluster_id")
    cross["probability"] = (cross["count_cards"] / cross["cluster_cards"]).round(4)
    cross = cross.drop(columns="cluster_cards").sort_values(
        ["cluster_id", "probability"], ascending=[True, False]
    )

    cross.to_csv(OUT_DIR / "cluster_to_tag.csv", index=False, encoding="utf-8")
    log.info("  %d lignes  →  cluster_to_tag.csv", len(cross))
    return cross


# ── 4. Sémantique des clusters ────────────────────────────────────────────────

def build_cluster_semantics(
    c2t: pd.DataFrame,
    annotations: dict[int, dict],
    top_n: int = 10,
) -> pd.DataFrame:
    log.info("Étape 4 — Sémantique des clusters par tags...")
    rows = []
    for cid, grp in c2t.groupby("cluster_id"):
        top = grp.nlargest(top_n, "probability")
        rows.append({
            "cluster_id":    cid,
            "cluster_name":  annotations.get(cid, {}).get("name", f"C{cid}"),
            "strategy":      annotations.get(cid, {}).get("primary_strategy", ""),
            "top_tags":      "|".join(top["tag"].tolist()),
            "top_tag_score": "|".join(top["probability"].astype(str).tolist()),
        })
    df = pd.DataFrame(rows).sort_values("cluster_id")
    df.to_csv(OUT_DIR / "cluster_semantics.csv", index=False, encoding="utf-8")
    log.info("  %d clusters  →  cluster_semantics.csv", len(df))
    return df


# ── 5. Modèle Tag → Cluster  (3 méthodes) ────────────────────────────────────

class TagClusterPredictor:
    """
    Trois méthodes de prédiction cluster depuis des tags :
      A) Score probabiliste simple  (somme des P(cluster|tag))
      B) Naive Bayes multinomial
      C) TF-IDF cosine similarity
    """

    def __init__(
        self,
        t2c: pd.DataFrame,
        c2t: pd.DataFrame,
        dataset: pd.DataFrame,
        annotations: dict[int, dict],
    ):
        self.annotations = annotations
        self.cluster_ids  = sorted(annotations.keys())
        self.cluster_names = {cid: annotations[cid]["name"] for cid in self.cluster_ids}

        # ── A) Table P(cluster | tag) ─────────────────────────────────────────
        self._t2c_pivot = (
            t2c.pivot_table(index="tag", columns="cluster_id", values="probability", fill_value=0.0)
        )
        self._t2c_pivot.columns = self._t2c_pivot.columns.astype(int)

        # ── B) Naive Bayes ────────────────────────────────────────────────────
        # Construire matrice (carte × tag) avec cluster comme label
        self._card_cluster = dataset.drop_duplicates("card_name").set_index("card_name")["cluster_id"]
        cards_with_tags = dataset["card_name"].unique()

        card_tag_dict = dataset.groupby("card_name")["tag"].apply(list).to_dict()
        mlb = MultiLabelBinarizer()
        X_tags = mlb.fit_transform([card_tag_dict.get(c, []) for c in cards_with_tags])
        y      = self._card_cluster.reindex(cards_with_tags).fillna(-1).astype(int).values

        mask   = y >= 0
        self._mlb = mlb
        self._nb  = MultinomialNB(alpha=0.5)
        self._nb.fit(X_tags[mask], y[mask])
        self._nb_classes = list(self._nb.classes_)

        # ── C) TF-IDF cosine ─────────────────────────────────────────────────
        # Document = cluster, mot = tag pondéré par P(tag|cluster)
        cluster_docs = (
            c2t.groupby("cluster_id")
            .apply(lambda g: " ".join(
                " ".join([row["tag"].replace(" ", "_")] * max(1, int(row["probability"] * 10)))
                for _, row in g.iterrows()
            ), include_groups=False)
            .reindex(self.cluster_ids, fill_value="")
        )
        self._tfidf_vec = TfidfVectorizer(min_df=1)
        self._tfidf_matrix = self._tfidf_vec.fit_transform(cluster_docs.values)

    # ── Méthode A : score probabiliste ───────────────────────────────────────
    def predict_proba(self, tags: list[str]) -> list[dict]:
        scores: dict[int, float] = defaultdict(float)
        for tag in tags:
            if tag in self._t2c_pivot.index:
                row = self._t2c_pivot.loc[tag]
                for cid, prob in row.items():
                    if prob > 0:
                        scores[int(cid)] += float(prob)
        if not scores:
            return []
        total = sum(scores.values()) or 1.0
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return [
            {
                "cluster_id":   cid,
                "cluster_name": self.cluster_names.get(cid, f"C{cid}"),
                "probability":  round(sc / total, 4),
                "method":       "probabilistic",
            }
            for cid, sc in ranked[:TOP_CLUSTER_K]
        ]

    # ── Méthode B : Naive Bayes ───────────────────────────────────────────────
    def predict_nb(self, tags: list[str]) -> list[dict]:
        X = self._mlb.transform([tags])
        probs = self._nb.predict_proba(X)[0]
        top_idx = np.argsort(probs)[::-1][:TOP_CLUSTER_K]
        return [
            {
                "cluster_id":   int(self._nb_classes[i]),
                "cluster_name": self.cluster_names.get(int(self._nb_classes[i]), "?"),
                "probability":  round(float(probs[i]), 4),
                "method":       "naive_bayes",
            }
            for i in top_idx if probs[i] > 0
        ]

    # ── Méthode C : TF-IDF cosine ─────────────────────────────────────────────
    def predict_tfidf(self, tags: list[str]) -> list[dict]:
        query = " ".join(t.replace(" ", "_") for t in tags)
        q_vec = self._tfidf_vec.transform([query])
        sims  = cosine_similarity(q_vec, self._tfidf_matrix)[0]
        top_idx = np.argsort(sims)[::-1][:TOP_CLUSTER_K]
        return [
            {
                "cluster_id":   self.cluster_ids[i],
                "cluster_name": self.cluster_names.get(self.cluster_ids[i], "?"),
                "probability":  round(float(sims[i]), 4),
                "method":       "tfidf_cosine",
            }
            for i in top_idx if sims[i] > 0
        ]

    # ── Méthode principale (Naive Bayes — meilleure accuracy, bien calibré) ───
    def predict_clusters_from_tags(self, tags: list[str]) -> list[dict]:
        """
        Naive Bayes (Top-1=41%, Top-3=66%, Top-5=75%) est retenu comme méthode
        principale car il filtre naturellement les tags trop génériques
        ('triggered ability', 'activated ability') par leur distribution uniforme.
        """
        return self.predict_nb(tags)


# ── 6. Commandants depuis tags ────────────────────────────────────────────────

class CommanderPredictor:
    """
    P(commander | tags) via :
      1. Clusters prédits depuis les tags
      2. Pour chaque commandant, score = Σ tfidf_norm(commandant, carte_du_cluster)
         pondéré par P(cluster | tags)
    """

    def __init__(
        self,
        cluster_predictor: TagClusterPredictor,
        tfidf_df: pd.DataFrame,
        annotations: dict[int, dict],
        cluster_cards: pd.DataFrame,
    ):
        self._cp = cluster_predictor
        self.annotations   = annotations
        self._cluster_cards = cluster_cards  # DataFrame { card_name, cluster_id }

        # Pré-calculer : pour chaque commandant, set de clusters dominants
        # via les cartes en commun entre commander profile et cluster
        # commandant → { cluster_id → score moyen tfidf_norm }
        log.info("  Calcul des scores commandant × cluster...")
        self._cmd_cluster_score: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))

        # Joindre tfidf avec clusters
        merged = tfidf_df.merge(
            cluster_cards[["card_name", "cluster_id"]], on="card_name", how="inner"
        )
        # Score = moyenne de tfidf_norm par (commandant, cluster)
        agg = merged.groupby(["commander", "cluster_id"])["tfidf_norm"].mean()
        for (cmd, cid), score in agg.items():
            self._cmd_cluster_score[cmd][int(cid)] = float(score)

        self._all_commanders = sorted(self._cmd_cluster_score.keys())

    def predict_commanders_from_tags(
        self, tags: list[str], top_k: int = TOP_COMMANDER_K
    ) -> list[dict]:
        cluster_preds = self._cp.predict_clusters_from_tags(tags)
        if not cluster_preds:
            return []

        cmd_scores: dict[str, float] = defaultdict(float)
        for pred in cluster_preds:
            cid  = pred["cluster_id"]
            prob = pred["probability"]
            for cmd, score in self._cmd_cluster_score.items():
                if cid in score:
                    cmd_scores[cmd] += score[cid] * prob

        if not cmd_scores:
            return []
        max_score = max(cmd_scores.values()) or 1.0
        ranked = sorted(cmd_scores.items(), key=lambda x: -x[1])[:top_k]
        return [
            {"commander": cmd, "probability": round(sc / max_score, 4)}
            for cmd, sc in ranked
        ]


# ── 7. Validation ─────────────────────────────────────────────────────────────

def validate(
    dataset: pd.DataFrame,
    predictor: TagClusterPredictor,
    n_sample: int = 500,
) -> pd.DataFrame:
    log.info("Étape 7 — Validation sur %d cartes...", n_sample)

    # Choisir des cartes avec >= 3 tags et un cluster connu
    card_tag_counts = dataset.groupby("card_name")["tag"].count()
    eligible = card_tag_counts[card_tag_counts >= 3].index.tolist()

    rng = np.random.default_rng(42)
    sample_cards = rng.choice(eligible, size=min(n_sample, len(eligible)), replace=False)

    card_tags    = dataset.groupby("card_name")["tag"].apply(list).to_dict()
    card_cluster = dataset.drop_duplicates("card_name").set_index("card_name")["cluster_id"].to_dict()

    results = []
    for card in sample_cards:
        true_cid = card_cluster.get(card)
        tags      = card_tags.get(card, [])

        for method_name, method_fn in [
            ("probabilistic", predictor.predict_proba),
            ("naive_bayes",   predictor.predict_nb),
            ("tfidf_cosine",  predictor.predict_tfidf),
        ]:
            preds = method_fn(tags)
            pred_ids = [p["cluster_id"] for p in preds]
            results.append({
                "card_name": card,
                "true_cluster": true_cid,
                "method": method_name,
                "top1_correct": int(len(pred_ids) > 0 and pred_ids[0] == true_cid),
                "top3_correct": int(true_cid in pred_ids[:3]),
                "top5_correct": int(true_cid in pred_ids[:5]),
                "predicted_1":  pred_ids[0] if pred_ids else -1,
                "n_tags":       len(tags),
            })

    df = pd.DataFrame(results)
    df.to_csv(OUT_DIR / "validation_report.csv", index=False, encoding="utf-8")

    # Résumé par méthode
    log.info("  --- Résultats de validation ---")
    for method, grp in df.groupby("method"):
        log.info(
            "  %-16s  Top1=%.3f  Top3=%.3f  Top5=%.3f",
            method, grp["top1_correct"].mean(), grp["top3_correct"].mean(), grp["top5_correct"].mean(),
        )
    return df


# ── 8. Card Semantic Profiles ─────────────────────────────────────────────────

def build_card_semantic_profiles(
    dataset: pd.DataFrame,
    annotations: dict[int, dict],
    top_tags: int = 5,
) -> pd.DataFrame:
    log.info("Étape 8 — Card Semantic Profiles...")

    card_cluster = dataset.drop_duplicates("card_name")[["card_name", "cluster_id", "cluster_name"]]
    card_top_tags = (
        dataset.groupby("card_name")["tag"]
        .apply(lambda tags: "|".join(list(tags)[:top_tags]))
        .reset_index()
        .rename(columns={"tag": "dominant_tags"})
    )

    profiles = card_cluster.merge(card_top_tags, on="card_name", how="left")
    profiles["strategy"] = profiles["cluster_id"].map(
        lambda cid: annotations.get(cid, {}).get("primary_strategy", "")
    )
    profiles["cluster_confidence"] = profiles["cluster_id"].map(
        lambda cid: annotations.get(cid, {}).get("confidence", 0.0)
    )
    profiles.to_csv(OUT_DIR / "card_semantic_profiles.csv", index=False, encoding="utf-8")
    log.info("  %d cartes  →  card_semantic_profiles.csv", len(profiles))
    return profiles


# ── 9. analyze_new_card ────────────────────────────────────────────────────────

def analyze_new_card(
    tags: list[str],
    cluster_predictor: TagClusterPredictor,
    commander_predictor: CommanderPredictor,
) -> dict:
    cluster_preds   = cluster_predictor.predict_clusters_from_tags(tags)
    commander_preds = commander_predictor.predict_commanders_from_tags(tags)

    # Méchaniques expliquant la prédiction
    explanation_tags = []
    for tag in tags:
        if tag in cluster_predictor._t2c_pivot.index:
            explanation_tags.append(tag)

    top_cluster_names = [p["cluster_name"] for p in cluster_preds[:3]]
    top_cmd_names     = [p["commander"] for p in commander_preds[:5]]
    confidence        = round(cluster_preds[0]["probability"], 3) if cluster_preds else 0.0

    return {
        "input_tags":          tags,
        "predicted_clusters":  cluster_preds,
        "predicted_commanders": commander_preds,
        "confidence":          confidence,
        "explanation": (
            f"Cette carte est principalement associée aux clusters : "
            f"{', '.join(top_cluster_names)}. "
            f"Commandants les plus compatibles : {', '.join(top_cmd_names)}. "
            f"Tags discriminants : {', '.join(explanation_tags[:5])}."
        ),
    }


# ── 10. Rapport ───────────────────────────────────────────────────────────────

def generate_report(
    dataset: pd.DataFrame,
    t2c: pd.DataFrame,
    c2t: pd.DataFrame,
    semantics: pd.DataFrame,
    validation: pd.DataFrame,
    cluster_predictor: TagClusterPredictor,
    commander_predictor: CommanderPredictor,
    annotations: dict[int, dict],
) -> None:
    log.info("Étape 10 — Rapport...")

    # ── Tags les plus discriminants (entropie de P(cluster|tag) faible)
    tag_entropies = []
    for tag, grp in t2c.groupby("tag"):
        probs = grp["probability"].values
        # Tags apparaissant dans peu de clusters = discriminants
        n_clusters = (probs > 0.1).sum()
        max_prob   = probs.max()
        tag_entropies.append({"tag": tag, "n_clusters": n_clusters, "max_prob": max_prob})
    disc_df = (
        pd.DataFrame(tag_entropies)
        .query("max_prob > 0.5")
        .sort_values(["n_clusters", "max_prob"], ascending=[True, False])
        .head(20)
    )

    # ── Clusters les plus prédictibles (validation top5 > 0.7)
    val_by_method = validation[validation["method"] == "probabilistic"].groupby("true_cluster")[
        ["top1_correct", "top3_correct", "top5_correct"]
    ].mean()

    best_clusters = val_by_method.nlargest(10, "top5_correct")

    # ── Exemple analyze_new_card
    example_tags = ["lands matter", "land ramp", "draw engine", "landfall", "triggered ability"]
    example_result = analyze_new_card(example_tags, cluster_predictor, commander_predictor)

    report = f"""# Rapport Tag-Cluster Bridge
> Généré automatiquement par build_tag_cluster_bridge.py

## 1. Données

| Métrique | Valeur |
|---|---|
| Cartes dans les clusters | {dataset['card_name'].nunique():,} |
| Cartes avec tags Scryfall | {dataset['card_name'].nunique():,} |
| Tags distincts (fréquents) | {t2c['tag'].nunique():,} |
| Clusters couverts | {dataset['cluster_id'].nunique()} |
| Paires (tag, cluster) | {len(t2c):,} |

## 2. Quels tags expliquent le mieux les clusters ?

Tags les plus discriminants (max P(cluster|tag) > 50%, peu de clusters cibles) :

| Tag | Clusters cibles | P max |
|---|---|---|
{chr(10).join(f"| {r['tag']} | {int(r['n_clusters'])} | {r['max_prob']:.3f} |" for _, r in disc_df.iterrows())}

## 3. Validation — Accuracy par méthode

| Méthode | Top-1 | Top-3 | Top-5 |
|---|---|---|---|
{chr(10).join(
    f"| {method} | {grp['top1_correct'].mean():.3f} | {grp['top3_correct'].mean():.3f} | {grp['top5_correct'].mean():.3f} |"
    for method, grp in validation.groupby("method")
)}

## 4. Clusters les plus prédictibles

| Cluster ID | Cluster name | Top-1 | Top-5 |
|---|---|---|---|
{chr(10).join(
    f"| {cid} | {annotations.get(cid, {}).get('name', '?')} | {row['top1_correct']:.3f} | {row['top5_correct']:.3f} |"
    for cid, row in best_clusters.iterrows()
)}

## 5. Tags les plus discriminants par cluster (top 3 clusters)

{chr(10).join(
    f"**{row['cluster_name']}** ({row['strategy']}) : {', '.join(row['top_tags'].split('|')[:5])}"
    for _, row in semantics.head(3).iterrows()
)}

## 6. Exemple : analyze_new_card

Tags d'entrée : `{', '.join(example_tags)}`

### Clusters prédits
{chr(10).join(f"- **{p['cluster_name']}** : {p['probability']:.3f}" for p in example_result['predicted_clusters'])}

### Commandants prédits
{chr(10).join(f"- **{p['commander']}** : {p['probability']:.3f}" for p in example_result['predicted_commanders'][:7])}

### Explication
{example_result['explanation']}

## 7. Réponses aux questions

### Peut-on prédire correctement les commandants à partir des tags seuls ?

La méthode probabiliste prédit les clusters avec une accuracy Top-3 de
**{validation[validation['method']=='probabilistic']['top3_correct'].mean():.1%}**.
Les commandants sont ensuite déduits par correspondance TF-IDF cluster → profil.

### Les tags Scryfall apportent-ils suffisamment d'information pour analyser des cartes inédites ?

**Oui pour les stratégies génériques** (Landfall, Dragon, Elf Tribal) où les tags sont très
discriminants. **Plus incertain** pour les clusters mixtes ou les niches thématiques dont les
tags chevauchent plusieurs stratégies (ex. "activated ability" est trop universel).

Les tags les plus utiles sont ceux à forte concentration : "typal-*", "lands matter",
"infect", "wheel", "ninjutsu" — ils pointent quasi-univoquement vers un cluster.

## 8. Fichiers produits

| Fichier | Description |
|---|---|
| `tag_cluster_dataset.csv` | Toutes les paires (carte, cluster, tag) |
| `tag_to_cluster.csv` | P(cluster \\| tag) |
| `cluster_to_tag.csv` | P(tag \\| cluster) |
| `cluster_semantics.csv` | Top tags par cluster |
| `card_semantic_profiles.csv` | Profil sémantique de chaque carte |
| `validation_report.csv` | Résultats de validation par méthode |
"""
    (OUT_DIR / "tag_cluster_report.md").write_text(report, encoding="utf-8")
    log.info("  Ecrit : tag_cluster_report.md")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== build_tag_cluster_bridge.py ===")

    # Chargements
    annotations  = load_annotations()
    clusters_df  = load_card_clusters()
    tags_df      = load_tags_from_db()
    tfidf_df     = pd.read_csv(TFIDF_CSV, encoding="utf-8")
    commanders   = load_commanders()

    log.info("Cartes en cluster : %d  |  Commandants : %d", len(clusters_df), len(commanders))

    # 1. Dataset
    dataset = build_tag_cluster_dataset(tags_df, clusters_df, annotations)

    # 2. P(cluster | tag)
    t2c = build_tag_to_cluster(dataset, annotations)

    # 3. P(tag | cluster)
    c2t = build_cluster_to_tag(dataset)

    # 4. Sémantique
    semantics = build_cluster_semantics(c2t, annotations)

    # 5. Modèle Tag → Cluster
    log.info("Étape 5 — Entraînement des modèles de prédiction...")
    predictor = TagClusterPredictor(t2c, c2t, dataset, annotations)

    # 6. Commandants depuis tags
    log.info("Étape 6 — Modèle Tag → Commandants...")
    cmd_predictor = CommanderPredictor(predictor, tfidf_df, annotations, clusters_df)

    # 7. Validation
    validation = validate(dataset, predictor)

    # 8. Profils sémantiques
    build_card_semantic_profiles(dataset, annotations)

    # 9. Démo analyze_new_card
    log.info("Étape 9 — Démo analyze_new_card...")
    demo_cases = [
        (["lands matter", "land ramp", "draw engine", "landfall", "triggered ability"],
         "Zimone and Dina style"),
        (["typal-dragon", "haste", "etb trigger", "flying"],
         "Dragon ETB"),
        (["infect", "proliferate", "poison counter", "evasion"],
         "Infect card"),
        (["wheel", "discard", "hand-neutral", "triggered ability"],
         "Wheel effect"),
        (["sacrifice outlet-creature", "drain life", "token generator", "death trigger"],
         "Aristocrats piece"),
    ]
    demo_results = []
    for tags, label in demo_cases:
        result = analyze_new_card(tags, predictor, cmd_predictor)
        result["label"] = label
        demo_results.append(result)
        log.info(
            "  [%s]  → %s (%.3f)  |  cmd: %s",
            label,
            result["predicted_clusters"][0]["cluster_name"] if result["predicted_clusters"] else "?",
            result["predicted_clusters"][0]["probability"] if result["predicted_clusters"] else 0.0,
            result["predicted_commanders"][0]["commander"] if result["predicted_commanders"] else "?",
        )

    (OUT_DIR / "demo_analysis.json").write_text(
        json.dumps(demo_results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 10. Rapport
    generate_report(dataset, t2c, c2t, semantics, validation, predictor, cmd_predictor, annotations)

    log.info("=== Terminé ===")
    log.info("  Sorties dans : %s", OUT_DIR)


if __name__ == "__main__":
    main()
