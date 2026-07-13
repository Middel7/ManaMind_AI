#!/usr/bin/env python3
"""Projette les données brutes mox_* vers deck_stat_commander + deck_stat_global.

Remplace import_decklists_to_postgres.py : même logique, source SQL au lieu de CSV.

Usage :
    # Un seul commandant (appelé automatiquement après chaque scrape)
    .venv\\Scripts\\python.exe scripts/mox_to_stats.py --commander "Doctor Doom, King of Latveria"

    # Recalcul complet (tous les commandants en base)
    .venv\\Scripts\\python.exe scripts/mox_to_stats.py

    # Recalcul des stats globales seulement (IDF)
    .venv\\Scripts\\python.exe scripts/mox_to_stats.py --global-only
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import os  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker, Session  # noqa: E402


def _make_session(db_url: str) -> Session:
    engine = create_engine(db_url)
    return sessionmaker(bind=engine)()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

BATCH_SIZE = 500


# ── Projection par commandant ─────────────────────────────────────────────────

def project_commander(commander: str, session: object) -> dict:
    """Projette mox_deck_cards + mox_decks → deck_stat_commander pour un commandant.

    Retourne {"decks": int, "cards": int} ou {"decks": 0} si rien en base.
    """
    # Compter les decks disponibles pour ce commandant
    row = session.execute(
        text("SELECT COUNT(*) FROM mox_decks WHERE commander = :cmd"),
        {"cmd": commander},
    ).scalar()
    total_decks = row or 0

    if total_decks == 0:
        return {"decks": 0, "cards": 0}

    # Calculer les taux d'inclusion :
    # pour chaque carte (hors commandant), compter le nb de decks qui la jouent.
    rows = session.execute(
        text("""
            SELECT
                dc.card_name,
                COUNT(DISTINCT dc.deck_id) AS decks_with_card
            FROM mox_deck_cards dc
            JOIN mox_decks d ON d.deck_id = dc.deck_id
            WHERE d.commander = :cmd
              AND dc.is_commander = 0
            GROUP BY dc.card_name
        """),
        {"cmd": commander},
    ).fetchall()

    if not rows:
        return {"decks": total_decks, "cards": 0}

    # Supprimer les anciennes stats de ce commandant
    session.execute(
        text("DELETE FROM deck_stat_commander WHERE commander = :cmd"),
        {"cmd": commander},
    )

    # Insérer par batch
    stat_rows = [
        {
            "commander": commander,
            "card_name": r.card_name,
            "decks_with_card": r.decks_with_card,
            "total_decks": total_decks,
            "inclusion_rate": round(r.decks_with_card / total_decks * 100, 4),
        }
        for r in rows
    ]

    for i in range(0, len(stat_rows), BATCH_SIZE):
        session.execute(
            text("""
                INSERT INTO deck_stat_commander
                    (commander, card_name, decks_with_card, total_decks, inclusion_rate)
                VALUES
                    (:commander, :card_name, :decks_with_card, :total_decks, :inclusion_rate)
                ON CONFLICT ON CONSTRAINT uq_deck_stat_commander_card DO UPDATE SET
                    decks_with_card = EXCLUDED.decks_with_card,
                    total_decks     = EXCLUDED.total_decks,
                    inclusion_rate  = EXCLUDED.inclusion_rate,
                    computed_at     = now()
            """),
            stat_rows[i : i + BATCH_SIZE],
        )

    session.commit()
    return {"decks": total_decks, "cards": len(stat_rows)}


# ── Stats globales (IDF) ──────────────────────────────────────────────────────

def recompute_global_stats(session: object) -> int:
    """Recalcule deck_stat_global depuis deck_stat_commander. Retourne le nb de cartes."""
    log.info("Recalcul des statistiques globales (IDF)…")

    session.execute(text("TRUNCATE TABLE deck_stat_global"))

    session.execute(text("""
        INSERT INTO deck_stat_global
            (card_name, decks_count, total_decks, global_frequency, commanders_count, idf)
        SELECT
            card_name,
            SUM(decks_with_card)                                                    AS decks_count,
            SUM(total_decks)                                                        AS total_decks,
            ROUND(
                (SUM(decks_with_card)::float / NULLIF(SUM(total_decks), 0) * 100)::numeric, 4
            )                                                                       AS global_frequency,
            COUNT(DISTINCT commander)                                               AS commanders_count,
            0.0                                                                     AS idf
        FROM deck_stat_commander
        GROUP BY card_name
        ON CONFLICT ON CONSTRAINT uq_deck_stat_global_card_name DO UPDATE SET
            decks_count      = EXCLUDED.decks_count,
            total_decks      = EXCLUDED.total_decks,
            global_frequency = EXCLUDED.global_frequency,
            commanders_count = EXCLUDED.commanders_count,
            computed_at      = now()
    """))
    session.commit()

    n_cmd = session.execute(
        text("SELECT COUNT(DISTINCT commander) FROM deck_stat_commander")
    ).scalar() or 1

    session.execute(text(f"""
        UPDATE deck_stat_global
        SET idf = LN({n_cmd}.0 / GREATEST(commanders_count, 1))
    """))
    session.commit()

    return session.execute(text("SELECT COUNT(*) FROM deck_stat_global")).scalar() or 0


# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="mox_* → deck_stat_commander + deck_stat_global")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL"),
                        help="URL SQLAlchemy (défaut : DATABASE_URL du .env)")
    parser.add_argument("--commander", default=None,
                        help="Projeter un seul commandant (omis = tous)")
    parser.add_argument("--global-only", action="store_true",
                        help="Recalculer uniquement deck_stat_global (IDF), sans toucher aux stats par commandant")
    args = parser.parse_args()

    if not args.db:
        sys.exit("aucune base : passez --db ou définissez DATABASE_URL")

    session = _make_session(args.db)
    try:
        if args.global_only:
            n = recompute_global_stats(session)
            log.info("deck_stat_global : %d cartes distinctes.", n)
            return

        if args.commander:
            commanders = [args.commander]
        else:
            rows = session.execute(
                text("SELECT DISTINCT commander FROM mox_decks WHERE commander IS NOT NULL ORDER BY commander")
            ).fetchall()
            commanders = [r[0] for r in rows]

        log.info("%d commandant(s) à projeter.", len(commanders))
        t0 = time.time()
        total_decks = 0
        total_cards = 0

        for i, cmd in enumerate(commanders, 1):
            stats = project_commander(cmd, session)
            total_decks += stats["decks"]
            total_cards += stats["cards"]

            if i % 50 == 0 or i == len(commanders):
                elapsed = time.time() - t0
                log.info(
                    "  [%d/%d] %d decks | %d entrées stat | %.0fs",
                    i, len(commanders), total_decks, total_cards, elapsed,
                )

        if not args.commander:
            n = recompute_global_stats(session)
            log.info("deck_stat_global : %d cartes distinctes.", n)

        log.info("Terminé en %.1fs.", time.time() - t0)
    finally:
        session.close()


if __name__ == "__main__":
    main()
