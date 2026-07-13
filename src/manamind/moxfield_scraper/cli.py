"""Point d'entrée en ligne de commande.

    uv run python -m manamind.moxfield_scraper init-db
    uv run python -m manamind.moxfield_scraper import-commanders data/TOPCOMMANDER.csv
    uv run python -m manamind.moxfield_scraper top --limit 200
    uv run python -m manamind.moxfield_scraper commander "The Ur-Dragon" --limit 500
    uv run python -m manamind.moxfield_scraper recent --limit 1000
    uv run python -m manamind.moxfield_scraper reparse ./data/moxfield_html

L'URL de la base est lue dans le .env du projet (DATABASE_URL), ou passée via --db.
"""

import argparse
import csv
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from . import db
from .pipeline import reparse_cache, scrape

import subprocess


def _project_commander(commander: str, db_url: str) -> None:
    """Projette un commandant vers deck_stat_commander via mox_to_stats.py."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "mox_to_stats.py"),
         "--db", db_url, "--commander", commander],
        check=False,
    )
    if result.returncode != 0:
        print(f"  [WARN] projection stats échouée pour « {commander} » (code {result.returncode})")


def _recompute_global_stats(db_url: str) -> None:
    """Recalcule deck_stat_global (IDF) via mox_to_stats.py."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "mox_to_stats.py"),
         "--db", db_url, "--global-only"],
        check=False,
    )
    if result.returncode != 0:
        print(f"  [WARN] recalcul stats globales échoué (code {result.returncode})")

# src/manamind/moxfield_scraper/cli.py -> racine du projet
ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}min"
    if m:
        return f"{m}min{s:02d}s"
    return f"{s}s"


def _read_topcommander(path: Path) -> list[dict]:
    """TOPCOMMANDER.csv -> lignes prêtes pour la table commanders."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("Name") or "").strip()
            if not name or name.lower() == "undefined":
                continue
            try:
                rank = int(row.get("Rank") or 0)
            except ValueError:
                continue
            rows.append({
                "name": name,
                "rank": rank,
                "color_identity": (row.get("Colors") or "").replace(",", ""),
            })
    return rows


def main(argv: list[str] | None = None) -> int:
    # Les consoles Windows sont en cp1252 : sans ça, un nom de carte accentué
    # fait planter le run entier sur un UnicodeEncodeError.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="manamind.moxfield_scraper", description="Scraper Moxfield -> base SQL"
    )
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL"),
                        help="URL SQLAlchemy (défaut : DATABASE_URL du .env)")
    parser.add_argument("--concurrency", type=int, default=7)
    parser.add_argument("--page-wait-ms", type=int, default=8000)
    parser.add_argument("--no-headless", action="store_true", help="Afficher le navigateur")
    parser.add_argument("--html-cache", type=Path, default=None,
                        help="Conserver le HTML brut ici (permet un reparse hors ligne)")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Créer les tables mox_*")

    p_import = sub.add_parser("import-commanders",
                              help="Charger TOPCOMMANDER.csv dans mox_commanders")
    p_import.add_argument("csv_path", type=Path)

    p_recent = sub.add_parser("recent", help="Decks récemment mis à jour, tous commandants")
    p_recent.add_argument("--limit", type=int, default=1000)
    p_recent.add_argument("--refresh", action="store_true",
                          help="Re-télécharger même les decks déjà en base")

    p_cmd = sub.add_parser("commander", help="Decks d'un commandant précis")
    p_cmd.add_argument("name")
    p_cmd.add_argument("--limit", type=int, default=500)
    p_cmd.add_argument("--refresh", action="store_true")

    p_top = sub.add_parser("top", help="Itérer sur la table commanders, par rang")
    p_top.add_argument("--limit", type=int, default=200, help="Decks par commandant")
    p_top.add_argument("--start", type=int, default=1, help="Rang de départ")
    p_top.add_argument("--refresh", action="store_true",
                       help="Re-traiter aussi les commandants déjà scrapés")

    p_reparse = sub.add_parser("reparse", help="Re-parser un cache HTML sans réseau")
    p_reparse.add_argument("cache_dir", type=Path)

    args = parser.parse_args(argv)

    if not args.db:
        parser.error("aucune base : passez --db ou définissez DATABASE_URL")

    engine = db.make_engine(args.db)
    headless = not args.no_headless

    common = dict(
        concurrency=args.concurrency,
        page_wait_ms=args.page_wait_ms,
        headless=headless,
        html_cache=args.html_cache,
    )

    if args.command == "init-db":
        db.init_schema(engine)
        print("Tables créées.")
        return 0

    if args.command == "import-commanders":
        rows = _read_topcommander(args.csv_path)
        n = db.upsert_commanders(engine, rows)
        print(f"{n} commandants importés.")
        return 0

    if args.command == "reparse":
        reparse_cache(engine, args.cache_dir)
        return 0

    if args.command == "recent":
        scrape(engine, limit=args.limit, skip_known=not args.refresh, **common)
        return 0

    if args.command == "commander":
        stats = scrape(engine, limit=args.limit, commander=args.name,
                       skip_known=not args.refresh, **common)
        if stats.saved > 0:
            _project_commander(args.name, args.db)
            _recompute_global_stats(args.db)
        return 0

    if args.command == "top":
        names = db.pending_commanders(engine, start_rank=args.start, refresh=args.refresh)
        if not names:
            print("Aucun commandant à traiter (tous déjà scrapés ? essayez --refresh).")
            return 0

        print(f"{len(names)} commandants à traiter, {args.limit} decks chacun.\n")
        started = time.time()

        for i, name in enumerate(names, 1):
            elapsed = time.time() - started
            eta = (elapsed / (i - 1)) * (len(names) - i + 1) if i > 1 else 0
            timing = (
                f"  (écoulé {_fmt_duration(elapsed)}, ETA {_fmt_duration(eta)})" if eta else ""
            )
            print(f"\n[{i}/{len(names)}] {name}{timing}")

            try:
                stats = scrape(engine, limit=args.limit, commander=name,
                               skip_known=not args.refresh, **common)
            except KeyboardInterrupt:
                print("\nInterrompu. L'état est en base, relancez pour reprendre.")
                return 130
            except Exception as exc:
                # Un commandant qui échoue (filtre introuvable, timeout) ne doit
                # pas faire tomber un run de 1500.
                print(f"  échec sur « {name} » : {exc}")
                continue

            db.mark_commander_scraped(engine, name, stats.saved)
            if stats.saved > 0:
                _project_commander(name, args.db)

        _recompute_global_stats(args.db)
        print(f"\nTerminé en {_fmt_duration(time.time() - started)}.")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
