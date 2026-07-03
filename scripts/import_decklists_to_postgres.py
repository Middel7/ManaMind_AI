"""
Import des decklists CSV vers PostgreSQL.

Source : C:\\Users\\fabie\\Desktop\\decklists_csv\\output_csv\\
Structure :
    output_csv/
    └── <Nom du commandant>/
        └── <deck_id>.csv   (délimiteur ";", BOM UTF-8)

Usage :
    uv run python scripts/import_decklists_to_postgres.py
    uv run python scripts/import_decklists_to_postgres.py --commander "Aesi, Tyrant of Gyre Strait"
    uv run python scripts/import_decklists_to_postgres.py --source-dir "D:/autre/dossier"
    uv run python scripts/import_decklists_to_postgres.py --recompute-stats-only
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from sqlalchemy import text
from mtgdb.db.engine import SessionLocal, engine
from mtgdb.db.models.deck_stats import DeckStatCommander, DeckStatGlobal

DEFAULT_SOURCE = Path(r"C:\Users\fabie\Desktop\decklists_csv\output_csv")
BATCH_SIZE = 500


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    return "".join(c for c in name if not unicodedata.combining(c)).lower().strip()


# ── Lecture d'un CSV deck ─────────────────────────────────────────────────────

def _parse_deck_csv(path: Path) -> tuple[list[str], list[tuple[str, int]]]:
    """
    Retourne (commanders, cartes_non_commandant).
    commanders : liste des noms de commandants dans ce fichier.
    cartes     : liste de (card_name, quantity) hors commandants.
    """
    commanders: list[str] = []
    cards: list[tuple[str, int]] = []
    try:
        with path.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                name = row.get("Card Name", "").strip()
                qty_raw = row.get("Quantity", "1").strip()
                is_cmd = row.get("Commander", "NO").strip().upper() == "YES"
                if not name:
                    continue
                try:
                    qty = int(qty_raw)
                except ValueError:
                    qty = 1
                if is_cmd:
                    commanders.append(name)
                else:
                    cards.append((name, qty))
    except Exception as e:
        print(f"    [WARN] Erreur lecture {path.name}: {e}")
    return commanders, cards


# ── Import d'un commandant ────────────────────────────────────────────────────

def import_commander(commander_name: str, source_dir: Path, session) -> dict:
    """
    Importe tous les decks d'un commandant depuis son dossier CSV.
    Retourne un dict de stats : {decks, cartes_total, erreurs}.
    """
    folder = source_dir / commander_name
    if not folder.is_dir():
        return {"decks": 0, "cartes": 0, "erreurs": 1}

    csv_files = list(folder.glob("*.csv"))
    if not csv_files:
        return {"decks": 0, "cartes": 0, "erreurs": 0}

    # Agrégation en mémoire : card_name → nombre de decks contenant la carte
    card_counts: dict[str, int] = {}
    card_canonical: dict[str, str] = {}  # norm → nom original
    total_decks = 0
    total_cartes = 0
    erreurs = 0

    for csv_path in csv_files:
        _, cards = _parse_deck_csv(csv_path)
        if not cards:
            erreurs += 1
            continue
        total_decks += 1
        # Dédupliquer les cartes dans un même deck avant de compter
        seen_in_deck: set[str] = set()
        for card_name, _qty in cards:
            norm = _normalize(card_name)
            if norm not in seen_in_deck:
                seen_in_deck.add(norm)
                card_counts[norm] = card_counts.get(norm, 0) + 1
                card_canonical[norm] = card_name
                total_cartes += 1

    if total_decks == 0:
        return {"decks": 0, "cartes": total_cartes, "erreurs": erreurs}

    # Supprimer les anciennes stats de ce commandant
    session.execute(
        text("DELETE FROM deck_stat_commander WHERE commander = :cmd"),
        {"cmd": commander_name},
    )

    # Insérer les nouvelles stats par batch
    rows = []
    for norm, count in card_counts.items():
        rows.append({
            "commander": commander_name,
            "card_name": card_canonical[norm],
            "decks_with_card": count,
            "total_decks": total_decks,
            "inclusion_rate": round(count / total_decks * 100, 4),
        })

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i: i + BATCH_SIZE]
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
            batch,
        )

    session.commit()
    return {"decks": total_decks, "cartes": total_cartes, "erreurs": erreurs}


# ── Recalcul des stats globales ───────────────────────────────────────────────

def recompute_global_stats(session) -> int:
    """
    Recalcule deck_stat_global depuis deck_stat_commander.
    Retourne le nombre de cartes distinctes insérées.
    """
    print("  Recalcul des statistiques globales...")
    session.execute(text("TRUNCATE TABLE deck_stat_global"))

    # Étape 1 : fréquences globales et commanders_count (sans IDF — dépend du total)
    session.execute(text("""
        INSERT INTO deck_stat_global
            (card_name, decks_count, total_decks, global_frequency, commanders_count, idf)
        SELECT
            card_name,
            SUM(decks_with_card)                                        AS decks_count,
            SUM(total_decks)                                            AS total_decks,
            ROUND(
                (SUM(decks_with_card)::float / NULLIF(SUM(total_decks), 0) * 100)::numeric, 4
            )                                                           AS global_frequency,
            COUNT(DISTINCT commander)                                   AS commanders_count,
            0.0                                                         AS idf
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

    # Étape 2 : IDF = ln(N_commandants / nb_commandants_jouant_la_carte)
    n_cmd = session.execute(text(
        "SELECT COUNT(DISTINCT commander) FROM deck_stat_commander"
    )).scalar() or 1
    session.execute(text(f"""
        UPDATE deck_stat_global
        SET idf = LN({n_cmd}.0 / GREATEST(commanders_count, 1))
    """))
    session.commit()

    result = session.execute(text("SELECT COUNT(*) FROM deck_stat_global")).scalar()
    return result or 0


# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Import decklists CSV → PostgreSQL")
    parser.add_argument("--commander", help="Importer un seul commandant")
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE), help="Dossier source")
    parser.add_argument("--recompute-stats-only", action="store_true",
                        help="Recalcule uniquement deck_stat_global sans re-importer")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    if not source_dir.is_dir():
        print(f"Dossier source introuvable : {source_dir}")
        sys.exit(1)

    with SessionLocal() as session:

        if args.recompute_stats_only:
            n = recompute_global_stats(session)
            print(f"Stats globales recalculées : {n} cartes distinctes.")
            return

        if args.commander:
            commanders = [args.commander]
        else:
            commanders = sorted(
                d.name for d in source_dir.iterdir() if d.is_dir()
            )

        total_commanders = len(commanders)
        total_decks = 0
        total_erreurs = 0
        t0 = time.time()

        print(f"Import de {total_commanders} commandant(s) depuis {source_dir}\n")

        for i, cmd in enumerate(commanders, 1):
            t_cmd = time.time()
            stats = import_commander(cmd, source_dir, session)
            elapsed = time.time() - t_cmd
            total_decks += stats["decks"]
            total_erreurs += stats["erreurs"]

            status = f"{stats['decks']:4d} decks  {stats['cartes']:6d} cartes  {elapsed:.1f}s"
            if stats["erreurs"]:
                status += f"  [{stats['erreurs']} erreurs]"
            line = f"  [{i:4d}/{total_commanders}] {cmd[:55]:<55} {status}"
            print(line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))

        print(f"\nImport terminé en {time.time() - t0:.0f}s")
        print(f"  {total_decks} decks importés, {total_erreurs} erreurs")

        if not args.commander:
            n = recompute_global_stats(session)
            print(f"  {n} cartes distinctes dans deck_stat_global")


if __name__ == "__main__":
    main()
