#!/usr/bin/env python3
"""
compute_commander_tfidf.py

Calcule et stocke idf, tfidf, tfidf_norm dans deck_stat_commander (PostgreSQL).
Régénère aussi commander_tfidf.csv (pour les scripts ML qui en ont encore besoin)
et commander_top_signatures.csv.

Formules :
  TF(card, commander)    = inclusion_rate / 100
  IDF(card)              = depuis deck_stat_global.idf
  TF-IDF                 = TF × IDF
  TF-IDF normalisé       = TF-IDF / max(TF-IDF du commandant)

Usage :
  uv run python scripts/compute_commander_tfidf.py
  uv run python scripts/compute_commander_tfidf.py --no-csv   (DB seulement)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from sqlalchemy import text
from src.manamind.db.engine import SessionLocal

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

STATS_DIR = ROOT / "data" / "stats"
TOP_SIGNATURES = 20


def compute_tfidf_in_db(session) -> int:
    """
    Calcule et écrit idf, tfidf, tfidf_norm dans deck_stat_commander.
    Retourne le nombre de lignes mises à jour.
    """
    n_cmd = session.execute(text(
        "SELECT COUNT(DISTINCT commander) FROM deck_stat_commander"
    )).scalar() or 1

    log.info("Étape 0/3 — Recalcul IDF dans deck_stat_global (N=%d commandants)...", n_cmd)
    session.execute(text(f"""
        UPDATE deck_stat_global dsg
        SET idf = LN({n_cmd}.0 / GREATEST(sub.cmd_count, 1))
        FROM (
            SELECT card_name, COUNT(DISTINCT commander) AS cmd_count
            FROM deck_stat_commander
            GROUP BY card_name
        ) sub
        WHERE dsg.card_name = sub.card_name
    """))
    session.commit()

    log.info("Étape 1/3 — Copie de l'IDF vers deck_stat_commander...")
    session.execute(text("""
        UPDATE deck_stat_commander dsc
        SET idf = dsg.idf
        FROM deck_stat_global dsg
        WHERE dsc.card_name = dsg.card_name
    """))
    session.execute(text("UPDATE deck_stat_commander SET idf = 0.0 WHERE idf IS NULL"))

    log.info("Étape 2/3 — Calcul TF-IDF...")
    session.execute(text("""
        UPDATE deck_stat_commander
        SET tfidf = ROUND(((inclusion_rate / 100.0) * idf)::numeric, 6)
    """))

    log.info("Étape 3/3 — Normalisation TF-IDF par commandant...")
    session.execute(text("""
        UPDATE deck_stat_commander dsc
        SET tfidf_norm = ROUND(
            (dsc.tfidf / NULLIF(mx.max_tfidf, 0))::numeric, 6
        )
        FROM (
            SELECT commander, MAX(tfidf) AS max_tfidf
            FROM deck_stat_commander
            GROUP BY commander
        ) mx
        WHERE dsc.commander = mx.commander
    """))
    session.execute(text("UPDATE deck_stat_commander SET tfidf_norm = 0.0 WHERE tfidf_norm IS NULL"))
    session.commit()

    n = session.execute(text("SELECT COUNT(*) FROM deck_stat_commander WHERE tfidf IS NOT NULL")).scalar()
    return n or 0


def write_tfidf_csv(session) -> None:
    """Régénère commander_tfidf.csv depuis la DB (pour les scripts ML)."""
    import csv
    path = STATS_DIR / "commander_tfidf.csv"
    log.info("Écriture de %s...", path.name)
    rows = session.execute(text("""
        SELECT commander, card_name, inclusion_rate, idf, tfidf, tfidf_norm
        FROM deck_stat_commander
        ORDER BY commander ASC, tfidf DESC
    """)).fetchall()
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["commander", "card_name", "inclusion_rate", "idf", "tfidf", "tfidf_norm"])
        for r in rows:
            writer.writerow([r.commander, r.card_name,
                             round(r.inclusion_rate, 4), round(r.idf, 6),
                             round(r.tfidf, 6), round(r.tfidf_norm, 6)])
    log.info("  %d lignes écrites dans %s", len(rows), path.name)


def write_top_signatures(session) -> None:
    """Régénère commander_top_signatures.csv."""
    import csv
    path = STATS_DIR / "commander_top_signatures.csv"
    log.info("Écriture de %s...", path.name)
    rows = session.execute(text(f"""
        SELECT commander, card_name, tfidf, tfidf_norm, rank
        FROM (
            SELECT commander, card_name, tfidf, tfidf_norm,
                   ROW_NUMBER() OVER (PARTITION BY commander ORDER BY tfidf DESC) AS rank
            FROM deck_stat_commander
            WHERE tfidf IS NOT NULL
        ) sub
        WHERE rank <= {TOP_SIGNATURES}
        ORDER BY commander ASC, rank ASC
    """)).fetchall()
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["commander", "rank", "card_name", "tfidf", "tfidf_norm"])
        for r in rows:
            writer.writerow([r.commander, r.rank, r.card_name,
                             round(r.tfidf, 6), round(r.tfidf_norm, 6)])
    log.info("  %d lignes écrites dans %s", len(rows), path.name)


def print_sample(session, commander: str = "Aesi, Tyrant of Gyre Strait") -> None:
    rows = session.execute(text("""
        SELECT card_name, inclusion_rate, idf, tfidf, tfidf_norm
        FROM deck_stat_commander
        WHERE commander = :cmd
        ORDER BY tfidf DESC
        LIMIT 10
    """), {"cmd": commander}).fetchall()
    if not rows:
        return
    log.info("--- Aperçu : %s ---", commander)
    for r in rows:
        log.info("  %-40s  IR=%6.2f%%  IDF=%.4f  TFIDF=%.4f  norm=%.4f",
                 r.card_name, r.inclusion_rate, r.idf, r.tfidf, r.tfidf_norm)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-csv", action="store_true",
                        help="Ne pas régénérer les CSV (DB seulement)")
    args = parser.parse_args()

    (ROOT / "logs").mkdir(exist_ok=True)
    STATS_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=== compute_commander_tfidf.py ===")

    with SessionLocal() as session:
        n = compute_tfidf_in_db(session)
        log.info("TF-IDF calculé pour %d lignes en DB.", n)

        if not args.no_csv:
            write_tfidf_csv(session)
            write_top_signatures(session)

        print_sample(session)

    log.info("=== Terminé ===")


if __name__ == "__main__":
    main()
