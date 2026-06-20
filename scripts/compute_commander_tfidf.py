#!/usr/bin/env python3
"""
compute_commander_tfidf.py

Génère les profils TF-IDF par commandant depuis les tables PostgreSQL
deck_stat_commander et deck_stat_global.

TF(card, commander)       = inclusion_rate / 100  (ratio 0-1)
TF-IDF(card, commander)   = TF × IDF
Normalized TF-IDF         = TF-IDF / max(TF-IDF du commandant)

Sorties :
  data/stats/commander_tfidf.csv          ← toutes les paires (commander, card)
  data/stats/commander_profiles/<slug>.csv ← top 500 par commandant
  data/stats/commander_profiles_json/<slug>.json
  data/stats/commander_summary.csv         ← résumé par commandant
  data/stats/commander_top_signatures.csv  ← top 20 cartes signatures
"""
from __future__ import annotations

import json
import logging
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.manamind.db.engine import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / "compute_commander_tfidf.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Constantes ───────────────────────────────────────────────────────────────
TOP_CARDS_PER_PROFILE = 500
TOP_SIGNATURES = 20
STATS_DIR = ROOT / "data" / "stats"
PROFILES_DIR = STATS_DIR / "commander_profiles"
JSON_DIR = STATS_DIR / "commander_profiles_json"

(ROOT / "logs").mkdir(exist_ok=True)
STATS_DIR.mkdir(parents=True, exist_ok=True)
PROFILES_DIR.mkdir(exist_ok=True)
JSON_DIR.mkdir(exist_ok=True)


def slugify(name: str) -> str:
    """Convertit un nom de commandant en slug ASCII snake_case."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-zA-Z0-9\s]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name


def load_from_db() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Charge deck_stat_commander et deck_stat_global depuis PostgreSQL."""
    log.info("Connexion à PostgreSQL...")
    with SessionLocal() as session:
        log.info("Chargement deck_stat_commander...")
        commander_df = pd.read_sql(
            text("SELECT commander, card_name, decks_with_card, total_decks, inclusion_rate FROM deck_stat_commander"),
            session.bind,
        )
        log.info("Chargement deck_stat_global...")
        global_df = pd.read_sql(
            text("SELECT card_name, decks_count, total_decks, global_frequency, commanders_count, idf FROM deck_stat_global"),
            session.bind,
        )
    log.info(
        "Chargé : %d paires (commander, carte) | %d cartes globales",
        len(commander_df),
        len(global_df),
    )
    return commander_df, global_df


def compute_tfidf(commander_df: pd.DataFrame, global_df: pd.DataFrame) -> pd.DataFrame:
    """Calcule TF-IDF et normalized TF-IDF pour chaque paire (commander, card)."""
    log.info("Calcul TF-IDF...")

    # TF = inclusion_rate en ratio 0-1 (DB stocke en pourcentage)
    df = commander_df.copy()
    df["tf"] = df["inclusion_rate"] / 100.0

    # Join avec IDF depuis deck_stat_global
    idf_map = global_df.set_index("card_name")["idf"]
    df["idf"] = df["card_name"].map(idf_map)

    missing = df["idf"].isna().sum()
    if missing > 0:
        log.warning("%d cartes sans IDF — remplacement par 0.0", missing)
        df["idf"] = df["idf"].fillna(0.0)

    df["tfidf"] = df["tf"] * df["idf"]

    # Normalized TF-IDF : score / max du commandant
    max_per_commander = df.groupby("commander")["tfidf"].transform("max")
    df["tfidf_norm"] = df["tfidf"] / max_per_commander.replace(0, 1)

    # Colonnes finales
    result = df[["commander", "card_name", "inclusion_rate", "idf", "tfidf", "tfidf_norm"]].copy()
    result = result.sort_values(["commander", "tfidf"], ascending=[True, False])
    result["inclusion_rate"] = result["inclusion_rate"].round(4)
    result["idf"] = result["idf"].round(6)
    result["tfidf"] = result["tfidf"].round(6)
    result["tfidf_norm"] = result["tfidf_norm"].round(6)

    log.info("TF-IDF calculé pour %d paires.", len(result))
    return result


def write_global_csv(df: pd.DataFrame) -> None:
    """Écrit commander_tfidf.csv (toutes les paires)."""
    path = STATS_DIR / "commander_tfidf.csv"
    df.to_csv(path, index=False, encoding="utf-8")
    log.info("Ecrit : %s (%d lignes)", path.name, len(df))


def write_commander_profiles(df: pd.DataFrame) -> None:
    """Écrit un CSV par commandant (top N cartes TF-IDF) + JSON."""
    commanders = df["commander"].unique()
    log.info("Génération des profils pour %d commandants...", len(commanders))

    for cmd in tqdm(commanders, desc="Profils CSV+JSON", unit="cmd"):
        slug = slugify(cmd)
        cmd_df = df[df["commander"] == cmd].head(TOP_CARDS_PER_PROFILE).copy()
        cmd_df.insert(0, "rank", range(1, len(cmd_df) + 1))

        # CSV
        csv_path = PROFILES_DIR / f"{slug}.csv"
        cmd_df[["rank", "card_name", "inclusion_rate", "idf", "tfidf", "tfidf_norm"]].to_csv(
            csv_path, index=False, encoding="utf-8"
        )

        # JSON
        top_json = cmd_df.head(TOP_SIGNATURES)[["card_name", "tfidf", "tfidf_norm"]].to_dict(orient="records")
        json_payload = {
            "commander": cmd,
            "top_cards": [
                {
                    "card_name": r["card_name"],
                    "tfidf": round(r["tfidf"], 4),
                    "tfidf_norm": round(r["tfidf_norm"], 4),
                }
                for r in top_json
            ],
        }
        json_path = JSON_DIR / f"{slug}.json"
        json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_commander_summary(df: pd.DataFrame, commander_df: pd.DataFrame) -> None:
    """Écrit commander_summary.csv — une ligne par commandant."""
    log.info("Calcul du résumé commandants...")
    rows = []
    for cmd, group in df.groupby("commander"):
        deck_count = int(commander_df[commander_df["commander"] == cmd]["total_decks"].iloc[0])
        top_row = group.iloc[0]
        rows.append({
            "commander": cmd,
            "deck_count": deck_count,
            "unique_cards": len(group),
            "mean_tfidf": round(group["tfidf"].mean(), 4),
            "max_tfidf": round(group["tfidf"].max(), 4),
            "top_card": top_row["card_name"],
        })
    summary = pd.DataFrame(rows).sort_values("commander")
    path = STATS_DIR / "commander_summary.csv"
    summary.to_csv(path, index=False, encoding="utf-8")
    log.info("Ecrit : %s (%d commandants)", path.name, len(summary))


def write_top_signatures(df: pd.DataFrame) -> None:
    """Écrit commander_top_signatures.csv — top 20 cartes signatures par commandant."""
    log.info("Calcul des signatures (top %d)...", TOP_SIGNATURES)
    signatures = (
        df.groupby("commander")
        .head(TOP_SIGNATURES)
        .copy()
    )
    signatures.insert(
        0, "rank",
        signatures.groupby("commander").cumcount() + 1
    )
    path = STATS_DIR / "commander_top_signatures.csv"
    signatures[["commander", "rank", "card_name", "tfidf", "tfidf_norm"]].to_csv(
        path, index=False, encoding="utf-8"
    )
    log.info("Ecrit : %s (%d lignes)", path.name, len(signatures))


def print_sample(df: pd.DataFrame, commander: str = "Galadriel, Light of Valinor") -> None:
    """Affiche un aperçu pour un commandant donné."""
    sample = df[df["commander"] == commander].head(10)
    if sample.empty:
        available = df["commander"].unique()[:5]
        sample = df[df["commander"] == available[0]].head(10)
        commander = available[0]
    log.info("--- Apercu : %s ---", commander)
    for _, row in sample.iterrows():
        log.info(
            "  %-40s  IR=%.2f%%  IDF=%.4f  TF-IDF=%.4f  norm=%.4f",
            row["card_name"],
            row["inclusion_rate"],
            row["idf"],
            row["tfidf"],
            row["tfidf_norm"],
        )


def main() -> None:
    log.info("=== compute_commander_tfidf.py ===")

    commander_df, global_df = load_from_db()
    tfidf_df = compute_tfidf(commander_df, global_df)

    write_global_csv(tfidf_df)
    write_commander_profiles(tfidf_df)
    write_commander_summary(tfidf_df, commander_df)
    write_top_signatures(tfidf_df)

    print_sample(tfidf_df)

    log.info("=== Termine ===")
    log.info("  commander_tfidf.csv        : %d lignes", len(tfidf_df))
    log.info("  Profils CSV+JSON           : %d commandants", tfidf_df["commander"].nunique())
    log.info("  Sorties dans               : %s", STATS_DIR)


if __name__ == "__main__":
    main()
