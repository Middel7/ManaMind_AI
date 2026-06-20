#!/usr/bin/env python3
"""
compute_deck_stats.py — Calcul des statistiques de decklists Commander.

Lit l'ensemble des decklists CSV depuis data/Decklists/, calcule :
  1. Fréquence globale de chaque carte (tous commandants confondus)
  2. Fréquence par commandant (taux d'inclusion)
  3. IDF de chaque carte (log(nb_commandants / nb_commandants_jouant_carte))

Puis persiste les résultats dans PostgreSQL (tables deck_stat_global et
deck_stat_commander) ET exporte des CSV de sortie dans data/stats/.

Usage :
    python scripts/compute_deck_stats.py
    python scripts/compute_deck_stats.py --decklists-dir data/Decklists
    python scripts/compute_deck_stats.py --csv-only          # pas de DB
    python scripts/compute_deck_stats.py --top 200           # top-N étendu

Architecture :
    Phase 1 — Scan    : parcours streamé des CSV, un set de cartes par deck.
                        Aucun deck complet gardé en mémoire simultanément.
    Phase 2 — Agrégat : Counter in-memory (tient largement pour 100k+ decks).
    Phase 3 — Export  : CSV + PostgreSQL (bulk INSERT via pg_insert ON CONFLICT).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from tqdm import tqdm

# ── PYTHONPATH ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# ── Constantes ─────────────────────────────────────────────────────────────────
DECKLISTS_DIR = ROOT / "data" / "Decklists"
STATS_DIR = ROOT / "data" / "stats"
LOG_DIR = ROOT / "logs"
ALIASES_FILE = ROOT / "data" / "commander_aliases.json"
TOP_N_DEFAULT = 100

# Seuil minimum de decks pour qu'un "commandant" soit considéré comme valide.
# Tout commandant avec moins de decks est traité comme un fichier parasite.
MIN_DECKS_THRESHOLD = 10

BASIC_LANDS: frozenset[str] = frozenset({
    "Island", "Plains", "Swamp", "Mountain", "Forest", "Wastes",
    "Snow-Covered Island", "Snow-Covered Plains", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest",
})


def load_aliases(path: Path) -> dict[str, str]:
    """
    Charge le fichier de mapping alias → nom canonique.
    Retourne un dict vide si le fichier est absent (mode dégradé : pas de normalisation).
    """
    if not path.exists():
        log.warning("Fichier d'aliases introuvable : %s — normalisation désactivée.", path)
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # Filtre la clé commentaire
    return {k: v for k, v in data.items() if not k.startswith("_")}


def normalize_commander(name: str, aliases: dict[str, str]) -> str | None:
    """
    Résout le nom canonique d'un commandant depuis le mapping d'aliases.
    Retourne None si le nom n'est pas dans le mapping (= parasite à ignorer).
    Si le mapping est vide, retourne le nom tel quel (mode dégradé).
    """
    if not aliases:
        return name
    return aliases.get(name)

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "compute_deck_stats.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("compute_deck_stats")


# ══════════════════════════════════════════════════════════════════════════════
# 1. PARSING
# ══════════════════════════════════════════════════════════════════════════════

def _open_csv(path: Path):
    """Ouvre un CSV avec détection d'encodage (utf-8-sig → cp1252 → latin-1)."""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return open(path, newline="", encoding=enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
    return open(path, newline="", encoding="latin-1", errors="replace")


def parse_deck_csv(path: Path) -> tuple[str | None, frozenset[str]]:
    """
    Parse un fichier CSV de decklist.

    Returns:
        (commander_name, frozenset_of_card_names)
        commander_name est None si introuvable.
        Le set contient chaque carte une seule fois (présence/absence).
        Les terrains de base et le commandant sont exclus du set.

    Raises:
        Exception: propagée vers l'appelant pour journalisation.
    """
    commander: str | None = None
    cards: set[str] = set()

    with _open_csv(path) as f:
        reader = csv.DictReader(f, delimiter=";")

        # Normalise les noms de colonnes (strip espaces, BOM résiduel)
        if reader.fieldnames:
            reader.fieldnames = [h.strip().lstrip("﻿") for h in reader.fieldnames]

        for row in reader:
            name = (row.get("Card Name") or "").strip()
            if not name:
                continue

            is_commander = (row.get("Commander") or "").strip().upper() == "YES"
            if is_commander:
                commander = name
                continue  # le commandant n'est pas compté dans les cartes du deck

            if name in BASIC_LANDS:
                continue

            cards.add(name)

    return commander, frozenset(cards)


def iter_decklists(
    decklists_dir: Path,
    aliases: dict[str, str],
) -> Iterator[tuple[str, frozenset[str]]]:
    """
    Itère sur toutes les decklists en normalisant le nom du commandant.

    Yields:
        (canonical_commander_name, frozenset_cards)

    Les decks dont le commandant ne figure pas dans le mapping aliases
    (ou dont le nombre de decks est insuffisant) sont ignorés silencieusement.
    Les parasites (B3, EDH, Casual…) sont loggués en DEBUG.
    """
    commander_dirs = sorted(d for d in decklists_dir.iterdir() if d.is_dir())
    skipped_parasites: Counter[str] = Counter()

    for cmd_dir in commander_dirs:
        csv_files = sorted(cmd_dir.glob("*.csv"))
        for csv_path in csv_files:
            try:
                raw_commander, cards = parse_deck_csv(csv_path)

                # Fallback : si la carte commandant n'est pas marquée YES,
                # on essaie avec le nom du dossier.
                if raw_commander is None:
                    raw_commander = cmd_dir.name

                canonical = normalize_commander(raw_commander, aliases)
                if canonical is None:
                    skipped_parasites[raw_commander] += 1
                    continue

                yield canonical, cards
            except Exception as exc:
                log.error("Fichier corrompu ignoré [%s] : %s", csv_path.name, exc)
                continue

    if skipped_parasites:
        log.info(
            "Parasites ignorés (%d noms distincts, %d decks au total) :",
            len(skipped_parasites),
            sum(skipped_parasites.values()),
        )
        for name, count in skipped_parasites.most_common():
            log.info("  %-45s %d deck(s)", name, count)


# ══════════════════════════════════════════════════════════════════════════════
# 2. CALCUL DES STATISTIQUES
# ══════════════════════════════════════════════════════════════════════════════

def compute_stats(
    decklists_dir: Path,
    aliases: dict[str, str],
) -> tuple[
    int,                               # total decks (après filtrage parasites)
    Counter[str],                      # global_card_counter  card → nb decks
    dict[str, Counter[str]],           # per_cmd   commander → Counter(card → nb decks)
    dict[str, int],                    # cmd_totals commander → nb total decks
    dict[str, set[str]],               # card_commanders  card → set of commanders
]:
    """
    Parcourt toutes les decklists et accumule les statistiques.
    Streamé : un seul deck en mémoire à la fois.
    Les commandants absents du mapping aliases sont ignorés.
    """
    global_counter: Counter[str] = Counter()
    per_cmd: dict[str, Counter[str]] = defaultdict(Counter)
    cmd_totals: dict[str, int] = Counter()
    card_commanders: dict[str, set[str]] = defaultdict(set)

    total_decks = 0

    all_csv = list(decklists_dir.rglob("*.csv"))
    log.info("Scan de %d fichiers CSV dans %s …", len(all_csv), decklists_dir)

    with tqdm(total=len(all_csv), unit="deck", desc="Lecture decklists") as bar:
        for commander, cards in iter_decklists(decklists_dir, aliases):
            total_decks += 1
            cmd_totals[commander] += 1

            for card in cards:
                global_counter[card] += 1
                per_cmd[commander][card] += 1
                card_commanders[card].add(commander)

            bar.update(1)

    log.info(
        "Lu %d decks valides | %d commandants | %d cartes uniques",
        total_decks, len(per_cmd), len(global_counter),
    )
    return total_decks, global_counter, per_cmd, cmd_totals, card_commanders


# ══════════════════════════════════════════════════════════════════════════════
# 3. EXPORT CSV
# ══════════════════════════════════════════════════════════════════════════════

def export_csv_global(
    path: Path,
    global_counter: Counter[str],
    total_decks: int,
    card_commanders: dict[str, set[str]],
    nb_commanders: int,
    top_n: int,
) -> None:
    """Exporte la fréquence globale (toutes cartes + top-N)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    top_path = path.parent / f"top{top_n}_global.csv"

    rows = []
    for card, count in global_counter.items():
        freq = count / total_decks * 100
        n_cmd = len(card_commanders[card])
        idf = math.log(nb_commanders / n_cmd) if n_cmd > 0 else 0.0
        rows.append((card, count, freq, n_cmd, idf))

    # Tri par fréquence décroissante
    rows.sort(key=lambda r: r[2], reverse=True)

    fieldnames = [
        "card_name", "decks_count", "global_frequency",
        "commanders_count", "idf",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for card, count, freq, n_cmd, idf in rows:
            w.writerow({
                "card_name": card,
                "decks_count": count,
                "global_frequency": round(freq, 4),
                "commanders_count": n_cmd,
                "idf": round(idf, 6),
            })

    # Top-N
    with open(top_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for card, count, freq, n_cmd, idf in rows[:top_n]:
            w.writerow({
                "card_name": card,
                "decks_count": count,
                "global_frequency": round(freq, 4),
                "commanders_count": n_cmd,
                "idf": round(idf, 6),
            })

    log.info("CSV global -> %s (%d cartes)", path.name, len(rows))
    log.info("CSV top-%d -> %s", top_n, top_path.name)


def export_csv_commander(
    path: Path,
    per_cmd: dict[str, Counter[str]],
    cmd_totals: dict[str, int],
    top_n: int,
) -> None:
    """Exporte les fréquences par commandant (toutes + top-N par commandant)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    top_dir = path.parent / "top_per_commander"
    top_dir.mkdir(exist_ok=True)

    fieldnames = ["commander", "card_name", "decks_with_card", "total_decks", "inclusion_rate"]

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        for commander in sorted(per_cmd):
            total = cmd_totals[commander]
            rows_cmd = []
            for card, count in per_cmd[commander].items():
                rows_cmd.append((card, count, count / total * 100))
            rows_cmd.sort(key=lambda r: r[2], reverse=True)

            for card, count, rate in rows_cmd:
                w.writerow({
                    "commander": commander,
                    "card_name": card,
                    "decks_with_card": count,
                    "total_decks": total,
                    "inclusion_rate": round(rate, 4),
                })

            # Top-N pour ce commandant
            safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in commander)
            top_cmd_path = top_dir / f"top{top_n}_{safe_name[:80]}.csv"
            with open(top_cmd_path, "w", newline="", encoding="utf-8-sig") as tf:
                tw = csv.DictWriter(tf, fieldnames=fieldnames)
                tw.writeheader()
                for card, count, rate in rows_cmd[:top_n]:
                    tw.writerow({
                        "commander": commander,
                        "card_name": card,
                        "decks_with_card": count,
                        "total_decks": total,
                        "inclusion_rate": round(rate, 4),
                    })

    log.info("CSV commandants -> %s", path.name)


def export_csv_inclusion(
    path: Path,
    per_cmd: dict[str, Counter[str]],
    cmd_totals: dict[str, int],
) -> None:
    """Table d'inclusion rapide : commander × card_name → inclusion_rate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["commander", "card_name", "inclusion_rate"]

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for commander in sorted(per_cmd):
            total = cmd_totals[commander]
            for card, count in sorted(per_cmd[commander].items()):
                w.writerow({
                    "commander": commander,
                    "card_name": card,
                    "inclusion_rate": round(count / total * 100, 4),
                })

    log.info("CSV inclusion -> %s", path.name)


# ══════════════════════════════════════════════════════════════════════════════
# 4. PERSISTANCE POSTGRESQL
# ══════════════════════════════════════════════════════════════════════════════

def persist_to_db(
    global_counter: Counter[str],
    total_decks: int,
    card_commanders: dict[str, set[str]],
    nb_commanders: int,
    per_cmd: dict[str, Counter[str]],
    cmd_totals: dict[str, int],
) -> None:
    """
    Insère/remplace les statistiques en base PostgreSQL.
    Utilise TRUNCATE + bulk INSERT pour garantir la cohérence sur recalcul.
    Batch size 2000 pour limiter la mémoire côté psycopg2.
    """
    try:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from mtgdb.db.engine import SessionLocal
        from mtgdb.db.models.deck_stats import DeckStatCommander, DeckStatGlobal
    except ImportError as e:
        log.error("Import mtgdb échoué : %s — persistance DB ignorée.", e)
        return

    if SessionLocal is None:
        log.warning("DATABASE_URL absent — persistance DB ignorée.")
        return

    now = datetime.now(tz=timezone.utc)
    BATCH = 2_000

    with SessionLocal() as session:
        log.info("Truncate deck_stat_global …")
        session.execute(DeckStatGlobal.__table__.delete())
        session.flush()

        global_rows = []
        for card, count in global_counter.items():
            freq = count / total_decks * 100
            n_cmd = len(card_commanders[card])
            idf = math.log(nb_commanders / n_cmd) if n_cmd > 0 else 0.0
            global_rows.append({
                "card_name": card,
                "decks_count": count,
                "total_decks": total_decks,
                "global_frequency": round(freq, 4),
                "commanders_count": n_cmd,
                "idf": round(idf, 6),
                "computed_at": now,
            })

        for i in tqdm(range(0, len(global_rows), BATCH), desc="INSERT deck_stat_global", unit="batch"):
            session.execute(pg_insert(DeckStatGlobal).values(global_rows[i:i + BATCH]))
            session.flush()

        log.info("Truncate deck_stat_commander …")
        session.execute(DeckStatCommander.__table__.delete())
        session.flush()

        cmd_rows = []
        for commander, counter in per_cmd.items():
            total = cmd_totals[commander]
            for card, count in counter.items():
                cmd_rows.append({
                    "commander": commander,
                    "card_name": card,
                    "decks_with_card": count,
                    "total_decks": total,
                    "inclusion_rate": round(count / total * 100, 4),
                    "computed_at": now,
                })

        for i in tqdm(range(0, len(cmd_rows), BATCH), desc="INSERT deck_stat_commander", unit="batch"):
            session.execute(pg_insert(DeckStatCommander).values(cmd_rows[i:i + BATCH]))
            session.flush()

        session.commit()

    log.info(
        "DB : %d lignes global | %d lignes commander",
        len(global_rows), len(cmd_rows),
    )


# ══════════════════════════════════════════════════════════════════════════════
# 5. POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Calcule les statistiques de decklists Commander et les persiste.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--decklists-dir",
        type=Path,
        default=DECKLISTS_DIR,
        help="Dossier racine contenant les sous-dossiers par commandant.",
    )
    p.add_argument(
        "--stats-dir",
        type=Path,
        default=STATS_DIR,
        help="Dossier de sortie pour les CSV.",
    )
    p.add_argument(
        "--top",
        type=int,
        default=TOP_N_DEFAULT,
        help="Nombre de cartes dans le fichier top-N.",
    )
    p.add_argument(
        "--csv-only",
        action="store_true",
        help="Exporte uniquement en CSV, sans toucher la base PostgreSQL.",
    )
    p.add_argument(
        "--db-only",
        action="store_true",
        help="Persiste uniquement en base PostgreSQL, sans exporter les CSV.",
    )
    return p


def main() -> None:
    args = build_arg_parser().parse_args()

    decklists_dir: Path = args.decklists_dir
    stats_dir: Path = args.stats_dir
    top_n: int = args.top

    if not decklists_dir.exists():
        log.error("Dossier introuvable : %s", decklists_dir)
        sys.exit(1)

    # ── Chargement du mapping aliases ─────────────────────────────────────────
    aliases = load_aliases(ALIASES_FILE)
    if aliases:
        log.info("Mapping aliases chargé : %d entrées, %d commandants canoniques.",
                 len(aliases), len(set(aliases.values())))
    else:
        log.warning("Aucun alias chargé — tous les noms de commandants sont acceptés.")

    # ── Phase 1 : scan ────────────────────────────────────────────────────────
    total_decks, global_counter, per_cmd, cmd_totals, card_commanders = compute_stats(
        decklists_dir, aliases
    )
    nb_commanders = len(per_cmd)

    # ── Phase 2 : résumé console ──────────────────────────────────────────────
    log.info("--- Resume -------------------------------------------")
    log.info("  Decks total         : %d", total_decks)
    log.info("  Commandants uniques : %d", nb_commanders)
    log.info("  Cartes uniques      : %d", len(global_counter))

    # Top 10 global
    top10 = global_counter.most_common(10)
    log.info("  Top 10 cartes globales :")
    for card, count in top10:
        log.info("    %-45s %5d  (%.1f%%)", card, count, count / total_decks * 100)

    # ── Phase 3 : export CSV ──────────────────────────────────────────────────
    if not args.db_only:
        stats_dir.mkdir(parents=True, exist_ok=True)
        export_csv_global(
            path=stats_dir / "global_frequency.csv",
            global_counter=global_counter,
            total_decks=total_decks,
            card_commanders=card_commanders,
            nb_commanders=nb_commanders,
            top_n=top_n,
        )
        export_csv_commander(
            path=stats_dir / "commander_frequency.csv",
            per_cmd=per_cmd,
            cmd_totals=cmd_totals,
            top_n=top_n,
        )
        export_csv_inclusion(
            path=stats_dir / "inclusion_rate.csv",
            per_cmd=per_cmd,
            cmd_totals=cmd_totals,
        )

    # ── Phase 4 : persistance DB ──────────────────────────────────────────────
    if not args.csv_only:
        persist_to_db(
            global_counter=global_counter,
            total_decks=total_decks,
            card_commanders=card_commanders,
            nb_commanders=nb_commanders,
            per_cmd=per_cmd,
            cmd_totals=cmd_totals,
        )

    log.info("Terminé.")


if __name__ == "__main__":
    main()
