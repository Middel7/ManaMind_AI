#!/usr/bin/env python3
"""
import_deck_cards.py

Importe les decklists individuelles depuis les CSV du Bureau vers la table deck_cards.

Stratégie :
  - COPY bulk via psycopg2 pour ~50x plus rapide qu'INSERT
  - Reprise sur crash : commandants déjà traités sont skippés
  - Batch de 50k lignes par COPY pour limiter la mémoire

Usage :
    uv run python scripts/import_deck_cards.py
    uv run python scripts/import_deck_cards.py --reset      # tout réimporter
    uv run python scripts/import_deck_cards.py --commander "Captain N'ghathrod"
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

CSV_ROOT = Path(r"C:\Users\fabie\Desktop\decklists_csv\output_csv")
BATCH_SIZE = 50_000   # lignes par COPY


def get_conn():
    """Connexion psycopg2 directe (plus rapide que SQLAlchemy pour COPY)."""
    import os
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    import psycopg2
    url = os.environ["DATABASE_URL"]
    # Convertir sqlalchemy URL en psycopg2 DSN si nécessaire
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(url)


def get_done_commanders(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT commander FROM deck_cards_import_progress")
        return {r[0] for r in cur.fetchall()}


def mark_commander_done(conn, commander: str, count: int) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO deck_cards_import_progress (commander, decks_imported, imported_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (commander) DO UPDATE
                SET decks_imported = EXCLUDED.decks_imported,
                    imported_at    = EXCLUDED.imported_at
        """, (commander, count, datetime.now(timezone.utc)))
    conn.commit()


def reset_commander(conn, commander: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM deck_cards WHERE commander = %s", (commander,))
        cur.execute("DELETE FROM deck_cards_import_progress WHERE commander = %s", (commander,))
    conn.commit()


def bulk_copy(conn, rows: list[tuple]) -> None:
    """Insère rows via COPY FROM STDIN (format texte tabulation)."""
    buf = io.StringIO()
    for deck_id, commander, card_name, is_commander in rows:
        # Échapper les caractères spéciaux COPY
        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
        buf.write(f"{esc(deck_id)}\t{esc(commander)}\t{esc(card_name)}\t{'t' if is_commander else 'f'}\n")
    buf.seek(0)
    with conn.cursor() as cur:
        cur.copy_from(buf, "deck_cards", columns=("deck_id", "commander", "card_name", "is_commander"))
    conn.commit()


def parse_csv(path: Path) -> list[tuple[str, str]]:
    """
    Retourne liste de (card_name, is_commander) depuis un CSV deck.
    Format attendu : Card Name;Quantity;Commander;...
    """
    cards = []
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                name = (row.get("Card Name") or "").strip()
                is_cmd = (row.get("Commander") or "").strip().upper() == "YES"
                if name:
                    cards.append((name, is_cmd))
    except Exception:
        pass
    return cards


def import_commander(conn, commander_dir: Path, done: set[str], reset: bool) -> int:
    """Importe tous les decks d'un commandant. Retourne le nombre de decks importés."""
    commander = commander_dir.name

    if commander in done and not reset:
        return 0

    if reset:
        reset_commander(conn, commander)

    csv_files = sorted(commander_dir.glob("*.csv"))
    if not csv_files:
        return 0

    batch: list[tuple] = []
    decks_count = 0

    for csv_path in csv_files:
        deck_id = csv_path.stem
        cards = parse_csv(csv_path)
        if not cards:
            continue
        for card_name, is_cmd in cards:
            batch.append((deck_id, commander, card_name, is_cmd))
        decks_count += 1

        if len(batch) >= BATCH_SIZE:
            bulk_copy(conn, batch)
            batch.clear()

    if batch:
        bulk_copy(conn, batch)

    mark_commander_done(conn, commander, decks_count)
    return decks_count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset",       action="store_true", help="Tout réimporter depuis zéro")
    parser.add_argument("--commander",   default=None,        help="Importer un seul commandant")
    parser.add_argument("--workers",     type=int, default=1, help="(réservé)")
    args = parser.parse_args()

    log.info("=== import_deck_cards.py ===")
    log.info("Source : %s", CSV_ROOT)

    if not CSV_ROOT.exists():
        log.error("Dossier source introuvable : %s", CSV_ROOT)
        sys.exit(1)

    conn = get_conn()
    log.info("Connexion DB OK")

    done = get_done_commanders(conn)
    log.info("Commandants déjà importés : %d", len(done))

    if args.commander:
        commander_dirs = [CSV_ROOT / args.commander]
        if not commander_dirs[0].exists():
            log.error("Dossier commandant introuvable : %s", commander_dirs[0])
            sys.exit(1)
    else:
        commander_dirs = sorted([d for d in CSV_ROOT.iterdir() if d.is_dir()])

    log.info("Commandants à traiter : %d", len(commander_dirs))

    t0 = time.time()
    total_decks   = 0
    total_commanders = 0
    skipped = 0

    for i, cmd_dir in enumerate(commander_dirs):
        commander = cmd_dir.name

        if commander in done and not args.reset and not args.commander:
            skipped += 1
            continue

        n = import_commander(conn, cmd_dir, done, args.reset)
        if n > 0:
            total_decks += n
            total_commanders += 1

        # Log de progression toutes les 50 commandants
        if (i + 1) % 50 == 0 or i == len(commander_dirs) - 1:
            elapsed = time.time() - t0
            rate = total_decks / elapsed if elapsed > 0 else 0
            eta_s = (len(commander_dirs) - i - 1) * (elapsed / (i + 1 - skipped + 0.01))
            log.info(
                "  [%d/%d] %d commandants | %d decks | %.0f decks/s | ETA ~%.0f min",
                i + 1, len(commander_dirs), total_commanders, total_decks, rate, eta_s / 60,
            )

    elapsed = time.time() - t0
    log.info("=== Terminé en %.1f min ===", elapsed / 60)
    log.info("  Commandants importés : %d", total_commanders)
    log.info("  Decks importés       : %d", total_decks)
    log.info("  Skippés (déjà faits) : %d", skipped)

    conn.close()


if __name__ == "__main__":
    main()
