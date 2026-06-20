#!/usr/bin/env python3
"""
build_ml_dataset.py

Construit un dataset ML prêt pour XGBoost à partir des embeddings Card2Vec,
des profils TF-IDF et des métadonnées Scryfall.

Features par paire (commandant, carte) :
  cosine_similarity     -- similarité cosinus entre v(commander) et v(card)
  tfidf_norm            -- score TF-IDF normalisé [0, 1]
  idf                   -- discriminance globale de la carte
  global_frequency      -- % de decks (toutes couleurs) contenant la carte
  color_identity_compat -- 1 si color_identity(carte) ⊆ color_identity(commandant)
  mana_value            -- coût de mana converti

Label :
  inclusion_rate        -- % de decks du commandant contenant la carte (régression)
  inclusion_rate_log    -- log1p(inclusion_rate) pour réduire le skew

Sorties :
  data/ml/train.csv
  data/ml/test.csv
  data/ml/feature_info.json  -- stats + noms des features
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.manamind.db.engine import SessionLocal  # noqa: E402

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "build_ml_dataset.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Chemins ──────────────────────────────────────────────────────────────────
EMB_DIR   = ROOT / "data" / "embeddings"
STATS_DIR = ROOT / "data" / "stats"
OUT_DIR   = ROOT / "data" / "ml"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TFIDF_CSV   = STATS_DIR / "commander_tfidf.csv"
CARD_NPY    = EMB_DIR / "card_embeddings.npy"
CMD_NPY     = EMB_DIR / "commander_embeddings.npy"
CMD_JSON    = EMB_DIR / "commander_embeddings.json"
CARD_IDX    = EMB_DIR / "card_index.json"

# ── Feature names ─────────────────────────────────────────────────────────────
FEATURES = [
    "cosine_similarity",
    "tfidf_norm",
    "idf",
    "global_frequency",
    "color_identity_compat",
    "mana_value",
]
LABEL       = "inclusion_rate"
LABEL_LOG   = "inclusion_rate_log"
TEST_SIZE   = 0.2
RANDOM_SEED = 42


# ── Chargement des embeddings ─────────────────────────────────────────────────

def load_embeddings() -> tuple[np.ndarray, np.ndarray, dict[str, int], dict[str, int]]:
    log.info("Chargement des embeddings...")
    card_matrix  = np.load(CARD_NPY).astype(np.float32)   # (16040, 128)
    cmd_matrix   = np.load(CMD_NPY).astype(np.float32)    # (34, 128)

    card_idx: dict[str, int] = json.loads(CARD_IDX.read_text(encoding="utf-8"))
    cmd_meta: dict            = json.loads(CMD_JSON.read_text(encoding="utf-8"))
    cmd_idx: dict[str, int]  = cmd_meta["commander_to_index"]

    log.info(
        "Embeddings : cartes=%d dim=%d | commandants=%d",
        card_matrix.shape[0], card_matrix.shape[1], cmd_matrix.shape[0],
    )

    # Pré-normaliser L2 pour que le produit scalaire == cosine similarity
    card_norms = np.linalg.norm(card_matrix, axis=1, keepdims=True)
    card_norms[card_norms == 0] = 1.0
    card_matrix_norm = card_matrix / card_norms

    cmd_norms = np.linalg.norm(cmd_matrix, axis=1, keepdims=True)
    cmd_norms[cmd_norms == 0] = 1.0
    cmd_matrix_norm = cmd_matrix / cmd_norms

    return card_matrix_norm, cmd_matrix_norm, card_idx, cmd_idx


# ── Chargement des métadonnées Scryfall ───────────────────────────────────────

def load_scryfall_metadata() -> pd.DataFrame:
    """Retourne un DataFrame { card_name, color_identity, mana_value }."""
    log.info("Chargement des metadonnees Scryfall...")
    with SessionLocal() as s:
        rows = s.execute(text(
            "SELECT name, color_identity, mana_value "
            "FROM scryfall_cards"
        )).fetchall()

    df = pd.DataFrame(rows, columns=["card_name", "color_identity", "mana_value"])
    # Dédupliquer par nom (plusieurs printings → même nom)
    df = df.drop_duplicates(subset="card_name").set_index("card_name")
    log.info("Scryfall : %d cartes uniques", len(df))
    return df


def load_commander_color_identity() -> dict[str, frozenset]:
    """Retourne { commander_name: frozenset(couleurs) }."""
    log.info("Chargement color_identity des commandants...")
    commanders = json.loads(CMD_JSON.read_text(encoding="utf-8"))["commanders"]
    with SessionLocal() as s:
        result: dict[str, frozenset] = {}
        for cmd in commanders:
            row = s.execute(
                text("SELECT color_identity FROM scryfall_cards WHERE name = :n LIMIT 1"),
                {"n": cmd},
            ).fetchone()
            result[cmd] = frozenset(row[0]) if row and row[0] else frozenset()
    log.info("Color identity chargee pour %d commandants", len(result))
    return result


def load_global_frequency() -> dict[str, float]:
    """Retourne { card_name: global_frequency } depuis deck_stat_global."""
    log.info("Chargement global_frequency depuis DB...")
    with SessionLocal() as s:
        rows = s.execute(text(
            "SELECT card_name, global_frequency FROM deck_stat_global"
        )).fetchall()
    return {r[0]: float(r[1]) for r in rows}


# ── Construction du dataset ───────────────────────────────────────────────────

def build_dataset(
    tfidf_df: pd.DataFrame,
    card_matrix: np.ndarray,
    cmd_matrix: np.ndarray,
    card_idx: dict[str, int],
    cmd_idx: dict[str, int],
    scryfall: pd.DataFrame,
    cmd_colors: dict[str, frozenset],
    global_freq: dict[str, float],
) -> pd.DataFrame:
    """
    Joint toutes les sources et calcule les features.
    Filtre les paires sans embedding.
    """
    log.info("Construction du dataset (%d paires TF-IDF)...", len(tfidf_df))

    # Filtrer les paires dont on a un embedding pour la carte
    has_emb = tfidf_df["card_name"].isin(card_idx)
    df = tfidf_df[has_emb].copy()
    log.info("Paires avec embedding : %d / %d", len(df), len(tfidf_df))

    # ── Cosine similarity ────────────────────────────────────────────────────
    # card_matrix et cmd_matrix sont déjà normalisés L2 → dot = cosine
    log.info("Calcul cosine similarity...")
    card_indices = df["card_name"].map(card_idx).to_numpy()
    cmd_indices  = df["commander"].map(cmd_idx).to_numpy()

    # Traitement par batch pour éviter une matrice (106k × 128) complète en RAM
    BATCH = 10_000
    cos_sims = np.empty(len(df), dtype=np.float32)
    for start in range(0, len(df), BATCH):
        end = min(start + BATCH, len(df))
        ci  = card_indices[start:end]
        cmi = cmd_indices[start:end]
        cos_sims[start:end] = (card_matrix[ci] * cmd_matrix[cmi]).sum(axis=1)

    df["cosine_similarity"] = cos_sims.round(6)

    # ── Features TF-IDF déjà présentes ──────────────────────────────────────
    # inclusion_rate en %, idf, tfidf_norm → déjà dans le df

    # ── Global frequency ────────────────────────────────────────────────────
    df["global_frequency"] = df["card_name"].map(global_freq).fillna(0.0)

    # ── Color identity compatibility ─────────────────────────────────────────
    log.info("Calcul color_identity_compat...")

    def card_colors(card_name: str) -> frozenset:
        if card_name not in scryfall.index:
            return frozenset()
        ci = scryfall.at[card_name, "color_identity"]
        return frozenset(ci) if ci else frozenset()

    card_color_cache: dict[str, frozenset] = {}

    def is_compatible(row: pd.Series) -> int:
        card = row["card_name"]
        if card not in card_color_cache:
            card_color_cache[card] = card_colors(card)
        cmd_ci = cmd_colors.get(row["commander"], frozenset())
        # Colorless (∅) est toujours compatible
        return int(card_color_cache[card] <= cmd_ci or not card_color_cache[card])

    df["color_identity_compat"] = df.apply(is_compatible, axis=1)

    # ── Mana value ───────────────────────────────────────────────────────────
    log.info("Ajout mana_value...")
    df["mana_value"] = df["card_name"].map(
        lambda n: float(scryfall.at[n, "mana_value"]) if n in scryfall.index else 0.0
    )

    # ── Label ────────────────────────────────────────────────────────────────
    # inclusion_rate déjà en % → on garde tel quel + version log
    df[LABEL_LOG] = np.log1p(df[LABEL])

    # ── Nettoyage final ──────────────────────────────────────────────────────
    final_cols = ["commander", "card_name"] + FEATURES + [LABEL, LABEL_LOG]
    df = df[final_cols].reset_index(drop=True)

    # Vérifier les NaN
    na_count = df[FEATURES].isna().sum()
    if na_count.any():
        log.warning("NaN dans les features :\n%s", na_count[na_count > 0])
        df[FEATURES] = df[FEATURES].fillna(0.0)

    log.info("Dataset final : %d lignes × %d colonnes", df.shape[0], df.shape[1])
    return df


# ── Split train/test ──────────────────────────────────────────────────────────

def split_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split stratifié par commandant : les commandants de test sont différents
    de ceux de train — simule la prédiction sur un commandant non vu.

    GroupShuffleSplit sur 'commander' garantit qu'un commandant
    est entièrement dans train OU dans test.
    """
    gss = GroupShuffleSplit(n_splits=1, test_size=TEST_SIZE, random_state=RANDOM_SEED)
    train_idx, test_idx = next(gss.split(df, groups=df["commander"]))
    train = df.iloc[train_idx].reset_index(drop=True)
    test  = df.iloc[test_idx].reset_index(drop=True)

    train_cmds = set(train["commander"].unique())
    test_cmds  = set(test["commander"].unique())
    log.info(
        "Split : train=%d lignes (%d commandants) | test=%d lignes (%d commandants)",
        len(train), len(train_cmds), len(test), len(test_cmds),
    )
    log.info("Commandants en test : %s", sorted(test_cmds))
    return train, test


# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== build_ml_dataset.py ===")

    # Chargement
    card_matrix, cmd_matrix, card_idx, cmd_idx = load_embeddings()
    tfidf_df   = pd.read_csv(TFIDF_CSV, encoding="utf-8")
    scryfall   = load_scryfall_metadata()
    cmd_colors = load_commander_color_identity()
    global_freq = load_global_frequency()

    # Construction
    dataset = build_dataset(
        tfidf_df, card_matrix, cmd_matrix,
        card_idx, cmd_idx, scryfall, cmd_colors, global_freq,
    )

    # Split
    train, test = split_dataset(dataset)

    # Sauvegarde
    train_path = OUT_DIR / "train.csv"
    test_path  = OUT_DIR / "test.csv"
    train.to_csv(train_path, index=False, encoding="utf-8")
    test.to_csv(test_path,  index=False, encoding="utf-8")
    log.info("Ecrit : %s", train_path.name)
    log.info("Ecrit : %s", test_path.name)

    # feature_info.json
    info = {
        "features": FEATURES,
        "label": LABEL,
        "label_log": LABEL_LOG,
        "n_train": len(train),
        "n_test": len(test),
        "commanders_train": sorted(train["commander"].unique().tolist()),
        "commanders_test":  sorted(test["commander"].unique().tolist()),
        "label_stats": {
            "mean":   round(dataset[LABEL].mean(), 4),
            "median": round(dataset[LABEL].median(), 4),
            "std":    round(dataset[LABEL].std(), 4),
            "min":    round(dataset[LABEL].min(), 4),
            "max":    round(dataset[LABEL].max(), 4),
        },
        "feature_stats": {
            col: {
                "mean":  round(float(dataset[col].mean()), 6),
                "std":   round(float(dataset[col].std()),  6),
                "min":   round(float(dataset[col].min()),  6),
                "max":   round(float(dataset[col].max()),  6),
            }
            for col in FEATURES
        },
    }
    info_path = OUT_DIR / "feature_info.json"
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Ecrit : %s", info_path.name)

    # Résumé
    log.info("=== Termine ===")
    log.info("  train.csv  : %d lignes", len(train))
    log.info("  test.csv   : %d lignes", len(test))
    log.info("  Features   : %s", FEATURES)
    log.info("  Label mean : %.4f%%  median : %.4f%%", dataset[LABEL].mean(), dataset[LABEL].median())

    # Apercu des features
    log.info("--- Apercu statistiques features ---")
    log.info("\n%s", dataset[FEATURES + [LABEL]].describe().round(4).to_string())


if __name__ == "__main__":
    main()
