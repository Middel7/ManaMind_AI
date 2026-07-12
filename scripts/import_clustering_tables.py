#!/usr/bin/env python3
"""Importe card_cluster_full.csv, tag_cluster_dataset.csv et tag_to_cluster.csv vers PostgreSQL.

Usage :
    uv run python scripts/import_clustering_tables.py

Les tables doivent exister (migration 20260712_add_clustering_tables appliquée).
"""
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from manamind.db.engine import engine, SessionLocal  # noqa: E402

DATA_DIR = ROOT / "data"
CLUST_DIR = DATA_DIR / "clustering"
TAG_DIR = DATA_DIR / "tag_cluster"


def import_card_clusters() -> None:
    """Importe card_cluster_full.csv → table card_clusters_global."""
    path = CLUST_DIR / "card_cluster_full.csv"
    if not path.exists():
        print(f"[SKIP] {path} introuvable")
        return

    df = pd.read_csv(path, encoding="utf-8")
    # Colonnes attendues : card_name, global_frequency, cluster_id, is_noise_fallback
    df["is_noise_fallback"] = df["is_noise_fallback"].fillna(False)
    # Convertir en bool (peut être True/False texte ou 0/1 entier selon la source)
    df["is_noise_fallback"] = df["is_noise_fallback"].map(
        lambda v: str(v).strip().lower() not in ("false", "0", "")
    )
    df["global_frequency"] = df["global_frequency"].fillna(0.0)
    # Supprimer les lignes bruit non assigné (cluster_id = -1)
    df = df[df["cluster_id"] >= 0].copy()

    with engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE card_clusters_global"))
        conn.commit()

    df[["card_name", "cluster_id", "global_frequency", "is_noise_fallback"]].to_sql(
        "card_clusters_global",
        engine,
        if_exists="append",
        index=False,
        method="multi",
        chunksize=2000,
    )
    print(f"[OK] card_clusters_global : {len(df)} lignes importées")


def import_card_tag_clusters() -> None:
    """Importe tag_cluster_dataset.csv → table card_tag_clusters."""
    path = TAG_DIR / "tag_cluster_dataset.csv"
    if not path.exists():
        print(f"[SKIP] {path} introuvable")
        return

    df = pd.read_csv(path, encoding="utf-8")
    # Colonnes attendues : card_name, cluster_id, cluster_name, tag
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE card_tag_clusters RESTART IDENTITY"))
        conn.commit()

    df[["card_name", "cluster_id", "cluster_name", "tag"]].to_sql(
        "card_tag_clusters",
        engine,
        if_exists="append",
        index=False,
        method="multi",
        chunksize=5000,
    )
    print(f"[OK] card_tag_clusters : {len(df)} lignes importées")


def import_tag_cluster_probabilities() -> None:
    """Importe tag_to_cluster.csv → table tag_cluster_probabilities."""
    path = TAG_DIR / "tag_to_cluster.csv"
    if not path.exists():
        print(f"[SKIP] {path} introuvable")
        return

    df = pd.read_csv(path, encoding="utf-8")
    # Colonnes attendues : tag, cluster_id, cluster_name, count_cards, probability
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE tag_cluster_probabilities RESTART IDENTITY"))
        conn.commit()

    df[["tag", "cluster_id", "cluster_name", "count_cards", "probability"]].to_sql(
        "tag_cluster_probabilities",
        engine,
        if_exists="append",
        index=False,
        method="multi",
        chunksize=5000,
    )
    print(f"[OK] tag_cluster_probabilities : {len(df)} lignes importées")


if __name__ == "__main__":
    import_card_clusters()
    import_card_tag_clusters()
    import_tag_cluster_probabilities()
    print("Import terminé.")
