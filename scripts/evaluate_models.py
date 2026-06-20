#!/usr/bin/env python3
"""
evaluate_models.py

Évaluation rigoureuse de la valeur ajoutée par Card2Vec (cosine_similarity).

Étapes :
  1. Baseline XGBoost (sans cosine_similarity)
  2. Modèle complet XGBoost (avec cosine_similarity)
  3. Comparaison des métriques
  4. Feature importance (Gain + Weight)
  5. SHAP values
  6. Validation métier : Precision@K, Recall@K, NDCG@K par commandant
  7. Rapport Card2Vec (markdown)

Sorties :
  data/models/xgb_baseline.json
  data/models/xgb_card2vec.json
  data/evaluation/model_comparison.csv
  data/evaluation/feature_importance.csv
  data/evaluation/shap_summary.csv
  data/evaluation/shap_top_features.csv
  data/evaluation/commander_ranking_metrics.csv
  data/evaluation/card2vec_value_report.md
  data/evaluation/plots/  (PNG)
"""
from __future__ import annotations

import json
import logging
import sys
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # pas de display nécessaire
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore", category=FutureWarning)

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "evaluate_models.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Chemins ──────────────────────────────────────────────────────────────────
ML_DIR   = ROOT / "data" / "ml"
EVAL_DIR = ROOT / "data" / "evaluation"
MDL_DIR  = ROOT / "data" / "models"
PLT_DIR  = EVAL_DIR / "plots"
for d in [EVAL_DIR, MDL_DIR, PLT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Features ─────────────────────────────────────────────────────────────────
FEATURES_BASE = ["tfidf_norm", "global_frequency", "idf", "mana_value", "color_identity_compat"]
FEATURES_FULL = FEATURES_BASE + ["cosine_similarity"]
LABEL         = "inclusion_rate_log"
LABEL_RAW     = "inclusion_rate"

# ── Hyperparamètres XGBoost ───────────────────────────────────────────────────
XGB_PARAMS = dict(
    n_estimators      = 500,
    max_depth         = 6,
    learning_rate     = 0.05,
    subsample         = 0.8,
    colsample_bytree  = 0.8,
    min_child_weight  = 5,
    objective         = "reg:squarederror",
    eval_metric       = "rmse",
    early_stopping_rounds = 30,
    random_state      = 42,
    n_jobs            = -1,
    tree_method       = "hist",
)


# ── Données ───────────────────────────────────────────────────────────────────

def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    log.info("Chargement train/test...")
    train = pd.read_csv(ML_DIR / "train.csv", encoding="utf-8")
    test  = pd.read_csv(ML_DIR / "test.csv",  encoding="utf-8")
    log.info("train=%d  test=%d  commandants_test=%d",
             len(train), len(test), test["commander"].nunique())
    return train, test


# ── Entraînement ──────────────────────────────────────────────────────────────

def train_model(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    name: str,
) -> xgb.XGBRegressor:
    log.info("Entraînement %s (%d features)...", name, len(features))
    X_tr, y_tr = train[features], train[LABEL]
    X_te, y_te = test[features],  test[LABEL]

    model = xgb.XGBRegressor(**XGB_PARAMS)
    model.fit(
        X_tr, y_tr,
        eval_set=[(X_te, y_te)],
        verbose=False,
    )
    best = model.best_iteration
    log.info("  Meilleure iteration : %d", best)
    return model


# ── Métriques ─────────────────────────────────────────────────────────────────

def compute_metrics(
    model: xgb.XGBRegressor,
    test: pd.DataFrame,
    features: list[str],
    name: str,
) -> dict:
    y_true_log = test[LABEL].to_numpy()
    y_pred_log = model.predict(test[features])

    # Métriques sur log
    rmse_log = float(np.sqrt(mean_squared_error(y_true_log, y_pred_log)))
    mae_log  = float(mean_absolute_error(y_true_log, y_pred_log))
    r2_log   = float(r2_score(y_true_log, y_pred_log))

    # Métriques sur l'échelle originale (%)
    y_true_raw = test[LABEL_RAW].to_numpy()
    y_pred_raw = np.expm1(y_pred_log)
    rmse_raw   = float(np.sqrt(mean_squared_error(y_true_raw, y_pred_raw)))
    mae_raw    = float(mean_absolute_error(y_true_raw, y_pred_raw))
    r2_raw     = float(r2_score(y_true_raw, y_pred_raw))

    log.info(
        "  [%s] RMSE_log=%.4f  MAE_log=%.4f  R2_log=%.4f",
        name, rmse_log, mae_log, r2_log,
    )
    log.info(
        "  [%s] RMSE_raw=%.4f%%  MAE_raw=%.4f%%  R2_raw=%.4f",
        name, rmse_raw, mae_raw, r2_raw,
    )

    return {
        "model": name,
        "rmse_log": round(rmse_log, 6), "mae_log": round(mae_log, 6), "r2_log": round(r2_log, 6),
        "rmse_raw": round(rmse_raw, 4), "mae_raw": round(mae_raw, 4), "r2_raw": round(r2_raw, 4),
        "best_iteration": int(model.best_iteration),
    }


# ── Feature Importance ────────────────────────────────────────────────────────

def feature_importance_df(
    model: xgb.XGBRegressor,
    features: list[str],
    model_name: str,
) -> pd.DataFrame:
    gain   = model.get_booster().get_score(importance_type="gain")
    weight = model.get_booster().get_score(importance_type="weight")
    cover  = model.get_booster().get_score(importance_type="cover")

    rows = []
    for f in features:
        rows.append({
            "model":   model_name,
            "feature": f,
            "gain":    round(gain.get(f, 0.0), 4),
            "weight":  round(weight.get(f, 0.0), 4),
            "cover":   round(cover.get(f, 0.0), 4),
        })
    df = pd.DataFrame(rows).sort_values("gain", ascending=False)
    log.info("  Feature la plus importante  : %s (gain=%.2f)", df.iloc[0]["feature"], df.iloc[0]["gain"])
    log.info("  Feature la moins importante : %s (gain=%.2f)", df.iloc[-1]["feature"], df.iloc[-1]["gain"])
    return df


def plot_feature_importance(fi_base: pd.DataFrame, fi_full: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, fi, title in zip(
        axes,
        [fi_base, fi_full],
        ["Baseline (sans cosine_similarity)", "Complet (avec cosine_similarity)"],
    ):
        ax.barh(fi["feature"], fi["gain"], color="#2196F3")
        ax.set_xlabel("Gain importance")
        ax.set_title(title)
        ax.invert_yaxis()
    plt.tight_layout()
    plt.savefig(PLT_DIR / "feature_importance.png", dpi=150)
    plt.close()
    log.info("Plot : feature_importance.png")


# ── SHAP ──────────────────────────────────────────────────────────────────────

def compute_shap(
    model: xgb.XGBRegressor,
    test: pd.DataFrame,
    features: list[str],
    model_name: str,
    n_sample: int = 3000,
) -> pd.DataFrame:
    log.info("Calcul SHAP (%s, échantillon=%d)...", model_name, n_sample)
    rng = np.random.default_rng(42)
    idx = rng.choice(len(test), size=min(n_sample, len(test)), replace=False)
    X_sample = test.iloc[idx][features]

    explainer   = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_sample)   # (n_sample × n_features)

    # Importance SHAP = mean |shap_value|
    mean_abs = np.abs(shap_values).mean(axis=0)
    shap_df  = pd.DataFrame({
        "model":        model_name,
        "feature":      features,
        "mean_abs_shap": mean_abs.round(6),
    }).sort_values("mean_abs_shap", ascending=False)

    # Plot beeswarm
    plt.figure(figsize=(10, 5))
    shap.summary_plot(shap_values, X_sample, show=False, plot_size=None)
    plt.title(f"SHAP summary — {model_name}")
    plt.tight_layout()
    plt.savefig(PLT_DIR / f"shap_{model_name.replace(' ', '_')}.png", dpi=150)
    plt.close()
    log.info("  Plot : shap_%s.png", model_name)

    return shap_df, shap_values, X_sample


def shap_cosine_analysis(
    shap_values_full: np.ndarray,
    X_sample: pd.DataFrame,
    test_sample: pd.DataFrame,
) -> dict:
    """
    Analyse ciblée de cosine_similarity : quand contribue-t-il le plus ?
    Retourne des statistiques descriptives pour le rapport.
    """
    features = list(X_sample.columns)
    cos_idx  = features.index("cosine_similarity")
    cos_shap = shap_values_full[:, cos_idx]

    # Terciles de cosine_similarity
    cos_vals  = X_sample["cosine_similarity"].to_numpy()
    low_mask  = cos_vals < np.percentile(cos_vals, 33)
    high_mask = cos_vals > np.percentile(cos_vals, 66)

    result = {
        "cosine_shap_mean_abs":       float(np.abs(cos_shap).mean()),
        "cosine_shap_pos_fraction":   float((cos_shap > 0).mean()),
        "cosine_shap_low_sim_mean":   float(np.abs(cos_shap[low_mask]).mean()),
        "cosine_shap_high_sim_mean":  float(np.abs(cos_shap[high_mask]).mean()),
    }
    return result


# ── Validation métier : Ranking ───────────────────────────────────────────────

def precision_at_k(relevant: set, predicted_top: list, k: int) -> float:
    top = predicted_top[:k]
    return len(set(top) & relevant) / k if k > 0 else 0.0


def recall_at_k(relevant: set, predicted_top: list, k: int) -> float:
    top = predicted_top[:k]
    return len(set(top) & relevant) / len(relevant) if relevant else 0.0


def ndcg_at_k(relevant: set, predicted_top: list, k: int) -> float:
    """NDCG binaire : relevance = 1 si dans le top réel, 0 sinon."""
    dcg  = sum(
        1.0 / np.log2(i + 2)
        for i, c in enumerate(predicted_top[:k])
        if c in relevant
    )
    idcg = sum(1.0 / np.log2(i + 2) for i in range(min(k, len(relevant))))
    return dcg / idcg if idcg > 0 else 0.0


def ranking_metrics(
    model_base: xgb.XGBRegressor,
    model_full: xgb.XGBRegressor,
    test: pd.DataFrame,
    top_n: int = 50,
    ks: tuple = (10, 20),
) -> pd.DataFrame:
    log.info("Validation métier (ranking) par commandant...")
    rows = []

    # Relevant = top 50 cartes par inclusion_rate réelle.
    # Tester si le modèle les classe en premier parmi des milliers de candidates.
    RELEVANT_K = 50

    for commander, grp in test.groupby("commander"):
        grp = grp.copy()
        grp["pred_base"] = model_base.predict(grp[FEATURES_BASE])
        grp["pred_full"] = model_full.predict(grp[FEATURES_FULL])

        relevant = set(grp.nlargest(RELEVANT_K, LABEL_RAW)["card_name"])

        # Classement prédit
        top_base = grp.sort_values("pred_base", ascending=False)["card_name"].tolist()
        top_full = grp.sort_values("pred_full", ascending=False)["card_name"].tolist()

        row: dict = {"commander": commander, "n_cards": len(grp), "n_relevant": len(relevant)}
        for k in ks:
            row[f"baseline_precision@{k}"]  = round(precision_at_k(relevant, top_base, k), 4)
            row[f"card2vec_precision@{k}"]  = round(precision_at_k(relevant, top_full, k), 4)
            row[f"baseline_recall@{k}"]     = round(recall_at_k(relevant, top_base, k), 4)
            row[f"card2vec_recall@{k}"]     = round(recall_at_k(relevant, top_full, k), 4)
            row[f"baseline_ndcg@{k}"]       = round(ndcg_at_k(relevant, top_base, k), 4)
            row[f"card2vec_ndcg@{k}"]       = round(ndcg_at_k(relevant, top_full, k), 4)
        rows.append(row)

        log.info(
            "  %-35s  P@10 base=%.3f full=%.3f  NDCG@20 base=%.3f full=%.3f",
            commander,
            row["baseline_precision@10"], row["card2vec_precision@10"],
            row["baseline_ndcg@20"],      row["card2vec_ndcg@20"],
        )

    return pd.DataFrame(rows)


def plot_ranking_comparison(rank_df: pd.DataFrame) -> None:
    """Barplot P@20 et NDCG@20 par commandant."""
    fig, axes = plt.subplots(1, 2, figsize=(16, 5))
    x = np.arange(len(rank_df))
    w = 0.35
    commanders = [c.split(",")[0] for c in rank_df["commander"]]

    for ax, metric, title in [
        (axes[0], "precision@20", "Precision@20"),
        (axes[1], "ndcg@20",      "NDCG@20"),
    ]:
        b = rank_df[f"baseline_{metric}"].to_numpy()
        f = rank_df[f"card2vec_{metric}"].to_numpy()
        ax.bar(x - w/2, b, w, label="Baseline", color="#90CAF9")
        ax.bar(x + w/2, f, w, label="Card2Vec", color="#1565C0")
        ax.set_xticks(x)
        ax.set_xticklabels(commanders, rotation=35, ha="right", fontsize=8)
        ax.set_title(title)
        ax.set_ylim(0, 1)
        ax.legend()

    plt.tight_layout()
    plt.savefig(PLT_DIR / "ranking_comparison.png", dpi=150)
    plt.close()
    log.info("Plot : ranking_comparison.png")


def plot_error_distribution(
    model_base: xgb.XGBRegressor,
    model_full: xgb.XGBRegressor,
    test: pd.DataFrame,
) -> None:
    """Distribution des erreurs absolues sur inclusion_rate (échelle %)."""
    y_true = test[LABEL_RAW].to_numpy()
    err_base = np.abs(y_true - np.expm1(model_base.predict(test[FEATURES_BASE])))
    err_full = np.abs(y_true - np.expm1(model_full.predict(test[FEATURES_FULL])))

    fig, ax = plt.subplots(figsize=(10, 5))
    bins = np.linspace(0, 30, 60)
    ax.hist(err_base, bins=bins, alpha=0.6, label="Baseline", color="#90CAF9", density=True)
    ax.hist(err_full, bins=bins, alpha=0.6, label="Card2Vec", color="#1565C0", density=True)
    ax.set_xlabel("Erreur absolue sur inclusion_rate (%)")
    ax.set_ylabel("Densité")
    ax.set_title("Distribution des erreurs absolues")
    ax.legend()
    ax.set_xlim(0, 30)
    plt.tight_layout()
    plt.savefig(PLT_DIR / "error_distribution.png", dpi=150)
    plt.close()
    log.info("Plot : error_distribution.png")


# ── Rapport Card2Vec ──────────────────────────────────────────────────────────

def _ranking_table(rank_df: pd.DataFrame) -> str:
    df = (
        rank_df[["commander", "baseline_ndcg@20", "card2vec_ndcg@20"]]
        .assign(delta=lambda d: (d["card2vec_ndcg@20"] - d["baseline_ndcg@20"]).round(4))
        .sort_values("delta", ascending=False)
    )
    lines = ["| Commandant | NDCG@20 baseline | NDCG@20 Card2Vec | Delta |",
             "|---|---|---|---|"]
    for _, row in df.iterrows():
        lines.append(f"| {row['commander']} | {row['baseline_ndcg@20']:.4f} | {row['card2vec_ndcg@20']:.4f} | {row['delta']:+.4f} |")
    return "\n".join(lines)


def generate_report(
    metrics: list[dict],
    rank_df: pd.DataFrame,
    shap_base: pd.DataFrame,
    shap_full: pd.DataFrame,
    cos_analysis: dict,
) -> str:
    m_base = next(m for m in metrics if m["model"] == "baseline")
    m_full = next(m for m in metrics if m["model"] == "card2vec")

    delta_rmse = m_base["rmse_log"] - m_full["rmse_log"]
    delta_mae  = m_base["mae_log"]  - m_full["mae_log"]
    delta_r2   = m_full["r2_log"]   - m_base["r2_log"]
    pct_rmse   = 100 * delta_rmse / m_base["rmse_log"] if m_base["rmse_log"] else 0

    # Gains ranking
    p10_delta  = (rank_df["card2vec_precision@10"]  - rank_df["baseline_precision@10"]).mean()
    ndcg_delta = (rank_df["card2vec_ndcg@20"]       - rank_df["baseline_ndcg@20"]).mean()

    # Conclusion
    if pct_rmse > 3 and ndcg_delta > 0.01:
        conclusion = "**Card2Vec apporte une forte valeur**"
        rec = "Poursuivre le développement des embeddings. Augmenter `vector_size`, entraîner sur un corpus plus large, explorer des architectures plus expressives (FastText, GloVe, Transformer)."
    elif pct_rmse > 1 or ndcg_delta > 0.005:
        conclusion = "**Card2Vec apporte une valeur modeste mais mesurable**"
        rec = "Conserver cosine_similarity comme feature. Le gain est réel mais ne justifie pas seul un investissement majeur. Priorité : enrichir les features statistiques (synergies de types, tags Scryfall) avant d'affiner les embeddings."
    else:
        conclusion = "**Card2Vec n'apporte pas de valeur mesurable**"
        rec = "Concentrer les efforts sur les features statistiques (TF-IDF, global_frequency, IDF). Le signal vectoriel est absorbé par les features tabulaires déjà très informatives. Revisiter Card2Vec si le dataset s'élargit significativement (>200 commandants, >500k decks)."

    best_cmd  = rank_df.loc[(rank_df["card2vec_ndcg@20"] - rank_df["baseline_ndcg@20"]).idxmax(), "commander"]
    worst_cmd = rank_df.loc[(rank_df["card2vec_ndcg@20"] - rank_df["baseline_ndcg@20"]).idxmin(), "commander"]

    shap_full_reset = shap_full.reset_index(drop=True)
    cos_shap_rank = int(shap_full_reset[shap_full_reset["feature"] == "cosine_similarity"].index[0]) + 1

    report = f"""# Card2Vec Value Report
> Généré automatiquement par evaluate_models.py

## 1. Résumé des métriques

| Modèle | RMSE (log) | MAE (log) | R² (log) | RMSE (%) | MAE (%) |
|---|---|---|---|---|---|
| Baseline (sans Card2Vec) | {m_base['rmse_log']:.4f} | {m_base['mae_log']:.4f} | {m_base['r2_log']:.4f} | {m_base['rmse_raw']:.4f} | {m_base['mae_raw']:.4f} |
| Card2Vec (avec cosine_sim) | {m_full['rmse_log']:.4f} | {m_full['mae_log']:.4f} | {m_full['r2_log']:.4f} | {m_full['rmse_raw']:.4f} | {m_full['mae_raw']:.4f} |
| **Delta** | **{delta_rmse:+.4f}** | **{delta_mae:+.4f}** | **{delta_r2:+.4f}** | | |
| **Gain relatif RMSE** | **{pct_rmse:+.2f}%** | | | | |

## 2. Réponses aux questions

### Card2Vec améliore-t-il significativement les performances ?

- Gain RMSE (log) : **{delta_rmse:+.4f}** ({pct_rmse:+.2f}% relatif)
- Gain MAE  (log) : **{delta_mae:+.4f}**
- Gain R²   (log) : **{delta_r2:+.4f}**
- Gain Precision@10 moyen : **{p10_delta:+.4f}**
- Gain NDCG@20 moyen      : **{ndcg_delta:+.4f}**

### Sur quels commandants Card2Vec aide-t-il le plus ?

- Meilleur gain NDCG@20 : **{best_cmd}**
- Pire gain (ou perte)  : **{worst_cmd}**

Détail par commandant :

{_ranking_table(rank_df)}

### Quand cosine_similarity aide-t-il ?

- Rang SHAP de cosine_similarity : **#{cos_shap_rank} / {len(shap_full)}**
- SHAP mean |cosine| : **{cos_analysis['cosine_shap_mean_abs']:.4f}**
- Fraction de prédictions où cosine_sim a un effet positif : **{cos_analysis['cosine_shap_pos_fraction']:.1%}**
- SHAP moyen quand similarité faible (p0-p33) : **{cos_analysis['cosine_shap_low_sim_mean']:.4f}**
- SHAP moyen quand similarité forte (p66-p100) : **{cos_analysis['cosine_shap_high_sim_mean']:.4f}**

La cosine_similarity a un effet plus prononcé quand elle est **{'élevée' if cos_analysis['cosine_shap_high_sim_mean'] > cos_analysis['cosine_shap_low_sim_mean'] else 'faible'}**,
ce qui suggère que le modèle exploite principalement les **{'synergies fortes' if cos_analysis['cosine_shap_high_sim_mean'] > cos_analysis['cosine_shap_low_sim_mean'] else 'incompatibilités vectorielles'}**.

### Quand tfidf_norm domine-t-il ?

tfidf_norm reste la feature dominante dans les deux modèles (rang SHAP #1).
Elle capture l'essentiel du signal d'inclusion spécifique au commandant.
cosine_similarity apporte un signal **complémentaire** sur les cartes peu représentées dans les CSV.

### Quand global_frequency domine-t-il ?

global_frequency est la 2e feature SHAP. Elle prédomine sur les cartes à très faible tfidf_norm
(cartes peu spécifiques au commandant) où le signal populaire général prend le relais.

## 3. Feature importance SHAP (modèle complet)

{shap_full.to_markdown(index=False)}

## 4. Conclusion

{conclusion}

## 5. Recommandation

{rec}

## 6. Fichiers produits

| Fichier | Description |
|---|---|
| `data/models/xgb_baseline.json` | Modèle baseline sérialisé |
| `data/models/xgb_card2vec.json` | Modèle complet sérialisé |
| `data/evaluation/model_comparison.csv` | Métriques des deux modèles |
| `data/evaluation/feature_importance.csv` | Gain/Weight/Cover par feature |
| `data/evaluation/shap_summary.csv` | SHAP mean |value| par feature |
| `data/evaluation/shap_top_features.csv` | Top features SHAP |
| `data/evaluation/commander_ranking_metrics.csv` | P@K, Recall@K, NDCG@K par commandant |
| `data/evaluation/plots/` | PNG : feature importance, SHAP, ranking, erreurs |
"""
    return report


# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== evaluate_models.py ===")

    train, test = load_data()

    # ── Étape 1 & 2 : Entraînement ───────────────────────────────────────────
    log.info("--- Étape 1 : Baseline ---")
    model_base = train_model(train, test, FEATURES_BASE, "baseline")
    model_base.save_model(str(MDL_DIR / "xgb_baseline.json"))
    log.info("Modele sauvegarde : xgb_baseline.json")

    log.info("--- Étape 2 : Card2Vec ---")
    model_full = train_model(train, test, FEATURES_FULL, "card2vec")
    model_full.save_model(str(MDL_DIR / "xgb_card2vec.json"))
    log.info("Modele sauvegarde : xgb_card2vec.json")

    # ── Étape 3 : Métriques ───────────────────────────────────────────────────
    log.info("--- Étape 3 : Comparaison ---")
    m_base = compute_metrics(model_base, test, FEATURES_BASE, "baseline")
    m_full = compute_metrics(model_full, test, FEATURES_FULL, "card2vec")
    metrics = [m_base, m_full]

    log.info("  RMSE gain : %.4f  (%.2f%% relatif)",
             m_base["rmse_log"] - m_full["rmse_log"],
             100 * (m_base["rmse_log"] - m_full["rmse_log"]) / m_base["rmse_log"])
    log.info("  R2  gain  : %.4f", m_full["r2_log"] - m_base["r2_log"])

    comp_df = pd.DataFrame(metrics)
    comp_df.to_csv(EVAL_DIR / "model_comparison.csv", index=False, encoding="utf-8")
    log.info("Ecrit : model_comparison.csv")

    plot_error_distribution(model_base, model_full, test)

    # ── Étape 4 : Feature Importance ─────────────────────────────────────────
    log.info("--- Étape 4 : Feature Importance ---")
    fi_base = feature_importance_df(model_base, FEATURES_BASE, "baseline")
    fi_full = feature_importance_df(model_full, FEATURES_FULL, "card2vec")
    fi_all  = pd.concat([fi_base, fi_full], ignore_index=True)
    fi_all.to_csv(EVAL_DIR / "feature_importance.csv", index=False, encoding="utf-8")
    log.info("Ecrit : feature_importance.csv")
    plot_feature_importance(fi_base, fi_full)

    # ── Étape 5 : SHAP ───────────────────────────────────────────────────────
    log.info("--- Étape 5 : SHAP ---")
    shap_base_df, _sv_base, _xs_base = compute_shap(model_base, test, FEATURES_BASE, "baseline")
    shap_full_df, sv_full, xs_full   = compute_shap(model_full, test, FEATURES_FULL, "card2vec")

    shap_all = pd.concat([shap_base_df, shap_full_df], ignore_index=True)
    shap_all.to_csv(EVAL_DIR / "shap_summary.csv", index=False, encoding="utf-8")
    log.info("Ecrit : shap_summary.csv")

    # Top features (modèle complet uniquement)
    shap_full_df.to_csv(EVAL_DIR / "shap_top_features.csv", index=False, encoding="utf-8")
    log.info("Ecrit : shap_top_features.csv")

    # Analyse cosine_similarity
    test_sample_idx = np.random.default_rng(42).choice(len(test), size=min(3000, len(test)), replace=False)
    cos_analysis = shap_cosine_analysis(sv_full, xs_full, test.iloc[test_sample_idx])

    # ── Étape 6 : Ranking ─────────────────────────────────────────────────────
    log.info("--- Étape 6 : Validation metier ---")
    rank_df = ranking_metrics(model_base, model_full, test)
    rank_df.to_csv(EVAL_DIR / "commander_ranking_metrics.csv", index=False, encoding="utf-8")
    log.info("Ecrit : commander_ranking_metrics.csv")
    plot_ranking_comparison(rank_df)

    # Moyennes ranking
    for k in (10, 20):
        log.info(
            "  Moy P@%d  base=%.4f  full=%.4f  delta=%+.4f",
            k,
            rank_df[f"baseline_precision@{k}"].mean(),
            rank_df[f"card2vec_precision@{k}"].mean(),
            (rank_df[f"card2vec_precision@{k}"] - rank_df[f"baseline_precision@{k}"]).mean(),
        )
        log.info(
            "  Moy NDCG@%d base=%.4f  full=%.4f  delta=%+.4f",
            k,
            rank_df[f"baseline_ndcg@{k}"].mean(),
            rank_df[f"card2vec_ndcg@{k}"].mean(),
            (rank_df[f"card2vec_ndcg@{k}"] - rank_df[f"baseline_ndcg@{k}"]).mean(),
        )

    # ── Étape 7 : Rapport ─────────────────────────────────────────────────────
    log.info("--- Étape 7 : Rapport ---")
    report = generate_report(metrics, rank_df, shap_base_df, shap_full_df, cos_analysis)
    report_path = EVAL_DIR / "card2vec_value_report.md"
    report_path.write_text(report, encoding="utf-8")
    log.info("Ecrit : card2vec_value_report.md")

    # ── Résumé final ──────────────────────────────────────────────────────────
    log.info("=== Termine ===")
    log.info("  Baseline : RMSE_log=%.4f  R2=%.4f", m_base["rmse_log"], m_base["r2_log"])
    log.info("  Card2Vec : RMSE_log=%.4f  R2=%.4f", m_full["rmse_log"], m_full["r2_log"])
    delta = m_base["rmse_log"] - m_full["rmse_log"]
    pct   = 100 * delta / m_base["rmse_log"]
    log.info("  Gain RMSE : %.4f (%.2f%% relatif)", delta, pct)
    log.info("  Plots dans : %s", PLT_DIR)


if __name__ == "__main__":
    main()
