#!/usr/bin/env python3
"""Importe card_neighbors.csv vers PostgreSQL (table card_neighbors)."""
import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from manamind.db.engine import engine  # noqa: E402

EMB_DIR = ROOT / "data" / "embeddings"


def main() -> None:
    path = EMB_DIR / "card_neighbors.csv"
    if not path.exists():
        print(f"[SKIP] {path} introuvable")
        return

    print("Lecture du CSV...")
    t0 = time.time()
    df = pd.read_csv(path, encoding="utf-8")
    print(f"  {len(df)} lignes lues en {time.time() - t0:.1f}s")

    # Vérification colonnes
    expected = {"card_name", "rank", "neighbor", "similarity"}
    assert expected.issubset(df.columns), (
        f"Colonnes manquantes : {expected - set(df.columns)}"
    )

    # Optimisation mémoire
    df["rank"] = df["rank"].astype("int16")
    df["similarity"] = df["similarity"].astype("float32")

    print("Vidage de la table existante...")
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE card_neighbors RESTART IDENTITY"))
        conn.commit()

    print("Import en cours (634k lignes, par chunks de 10 000)...")
    t1 = time.time()
    df.to_sql(
        "card_neighbors",
        engine,
        if_exists="append",
        index=False,
        method="multi",
        chunksize=10_000,
    )
    print(
        f"[OK] card_neighbors : {len(df)} lignes importées en {time.time() - t1:.1f}s"
    )


if __name__ == "__main__":
    main()
