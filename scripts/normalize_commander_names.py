#!/usr/bin/env python3
"""Réconcilie les noms de commandants entre deck_cards et commanders.

Les deux tables devaient écrire un commandant de la même façon, et ne le
faisaient plus : 987 noms des decks scrapés n'avaient aucune ligne dans
commanders, si bien que l'écran d'administration comptait 2 618 commandants
« uniques » pour 1 721 « enregistrés ». Trois causes, trois corrections :

  1. Paires « A / B ». Moxfield sépare désormais les deux commandants d'une
     paire par un trait seul, que le parser ne convertissait pas. Ces decks
     passent à la forme canonique « A & B », noms triés — celle de
     commanders, de deck_stat_commander et de l'analyse des decks.
  2. Noms de commanders non canoniques : paire mal ordonnée, ou recto-verso
     écrit « A & B ». Ils prennent la même forme que dans deck_cards.
  3. Recto-verso réduit à sa face avant dans commanders (« Eddie Brock »),
     quand les decks portent le nom complet (« Eddie Brock // Venom, Lethal
     Protector »). La ligne prend le nom complet.

Puis :
  4. Les commandants présents dans au moins MIN_DECKS decks, mais absents de
     commanders, y sont inscrits. Sous ce seuil, ce sont surtout des saisies
     fantaisistes (« Ganax & Treasures »).
  5. Les statistiques des commandants renommés sont recalculées, et l'IDF
     global avec elles.

Quand deux lignes de commanders fusionnent, la plus renseignée l'emporte
champ par champ : meilleur rang, scrape le plus récent, premier scrape le plus
ancien.

Usage (sur le poste de travail, qui porte deck_cards) :
    .venv\\Scripts\\python.exe scripts/normalize_commander_names.py          # simulation
    .venv\\Scripts\\python.exe scripts/normalize_commander_names.py --yes    # applique

Après application, relancer « Réentraîner » dans l'administration : les
regroupements TF-IDF sont indexés par nom de commandant.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from manamind.moxfield_scraper.parser import normalize_commander_sep  # noqa: E402

# Sous ce nombre de decks, un nom absent de commanders n'y est pas inscrit.
MIN_DECKS = 5


def _merge_commander(conn, old: str, new: str) -> str:
    """Renomme `old` en `new` dans commanders, en fusionnant si `new` existe."""
    exists = conn.execute(text("SELECT 1 FROM commanders WHERE name = :n"), {"n": new}).fetchone()
    if not exists:
        conn.execute(text("UPDATE commanders SET name = :new WHERE name = :old"),
                     {"new": new, "old": old})
        return "renommé"
    conn.execute(text("""
        UPDATE commanders AS t SET
            rank               = LEAST(t.rank, o.rank),
            color_identity     = COALESCE(t.color_identity, o.color_identity),
            decks_extracted    = GREATEST(t.decks_extracted, o.decks_extracted),
            first_extracted_at = LEAST(t.first_extracted_at, o.first_extracted_at),
            last_scraped_at    = GREATEST(t.last_scraped_at, o.last_scraped_at)
        FROM commanders AS o
        WHERE t.name = :new AND o.name = :old
    """), {"new": new, "old": old})
    conn.execute(text("DELETE FROM commanders WHERE name = :old"), {"old": old})
    return "fusionné"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="Appliquer (sinon simulation)")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()
    if not args.db:
        sys.exit("aucune base : passez --db ou définissez DATABASE_URL")

    engine = create_engine(args.db)
    touched_stats: set[str] = set()

    with engine.begin() as conn:
        deck_names = {
            r.commander: r.decks for r in conn.execute(text("""
                SELECT commander, COUNT(DISTINCT deck_id) AS decks
                FROM deck_cards WHERE commander IS NOT NULL GROUP BY commander
            """))
        }

        # ── 1. deck_cards : forme canonique ──────────────────────────────────
        renames = {old: normalize_commander_sep(old) for old in deck_names}

        # Recto-verso : l'import initial du 14 juillet 2026 n'a retenu que la
        # face avant (« Kuja, Genome Sorcerer »), les scrapes suivants écrivent
        # le nom complet. Les deux moitiés d'un même commandant se rejoignent
        # sous le nom complet, quand il n'y en a qu'un possible.
        by_front: dict[str, set[str]] = defaultdict(set)
        for new in renames.values():
            if " // " in new and "&" not in new:
                by_front[new.split(" // ")[0]].add(new)
        dfc_full = {front: next(iter(fulls)) for front, fulls in by_front.items()
                    if len(fulls) == 1}
        renames = {old: dfc_full.get(new, new) for old, new in renames.items()}

        renames = {old: new for old, new in renames.items() if new != old}
        pairs = sum(1 for o, n in renames.items() if " / " in o)
        decks_moved = sum(deck_names[o] for o in renames)
        print(f"1. deck_cards : {len(renames)} noms à réécrire ({decks_moved} decks), "
              f"dont {pairs} paires « A / B » et {len(renames) - pairs} autres")
        for old, new in list(renames.items())[:5]:
            print(f"     {old!r}\n  -> {new!r}")
        for old in [o for o in renames if " / " not in o][:5]:
            print(f"     {old!r}\n  -> {renames[old]!r}")

        if args.yes:
            for old, new in renames.items():
                conn.execute(text("UPDATE deck_cards SET commander = :new WHERE commander = :old"),
                             {"new": new, "old": old})
                touched_stats.update((old, new))

        # L'état des noms de decks après l'étape 1, pour les étapes suivantes.
        after: dict[str, int] = defaultdict(int)
        for name, n in deck_names.items():
            after[renames.get(name, name)] += n

        # ── 2. commanders : forme canonique ──────────────────────────────────
        cmd_names = [r[0] for r in conn.execute(text("SELECT name FROM commanders"))]
        cmd_renames = {n: normalize_commander_sep(n) for n in cmd_names}
        cmd_renames = {o: n for o, n in cmd_renames.items() if n != o}
        print(f"\n2. commanders : {len(cmd_renames)} noms non canoniques")
        for old, new in list(cmd_renames.items())[:5]:
            print(f"     {old!r}\n  -> {new!r}")
        if args.yes:
            for old, new in cmd_renames.items():
                _merge_commander(conn, old, new)

        # ── 3. commanders : recto-verso réduit à sa face avant ───────────────
        current = {cmd_renames.get(n, n) for n in cmd_names}
        # Une seule écriture complète possible (dfc_full) : sinon, impossible
        # de savoir laquelle est la bonne, la ligne reste en l'état.
        dfc = {front: full for front, full in dfc_full.items() if front in current}
        print(f"\n3. commanders : {len(dfc)} recto-verso réduits à leur face avant")
        for front, full in list(dfc.items())[:5]:
            print(f"     {front!r} -> {full!r}")
        if args.yes:
            for front, full in dfc.items():
                _merge_commander(conn, front, full)
        current = (current - set(dfc)) | set(dfc.values())

        # ── 4. Commandants absents de commanders ─────────────────────────────
        missing = sorted(
            ((n, d) for n, d in after.items() if n not in current and d >= MIN_DECKS),
            key=lambda x: -x[1],
        )
        ignored = sum(1 for n, d in after.items() if n not in current and d < MIN_DECKS)
        print(f"\n4. {len(missing)} commandants à inscrire (≥ {MIN_DECKS} decks), "
              f"{ignored} ignorés sous le seuil")
        for name, decks in missing[:8]:
            print(f"     {decks:>6}  {name}")
        if args.yes and missing:
            conn.execute(
                text("INSERT INTO commanders (name) VALUES (:n) ON CONFLICT (name) DO NOTHING"),
                [{"n": n} for n, _ in missing],
            )

        final = current | {n for n, _ in missing}
        orphans = sum(1 for n in after if n not in final)
        print(f"\nAprès correction : {len(final)} commandants enregistrés, "
              f"{orphans} noms de decks encore sans ligne (tous sous {MIN_DECKS} decks)")

    if not args.yes:
        print("\nSimulation : rien n'a été écrit. Relancer avec --yes pour appliquer.")
        return

    # ── 5. Statistiques ──────────────────────────────────────────────────────
    import mox_to_stats  # noqa: E402

    session = sessionmaker(bind=engine)()
    try:
        print(f"\n5. Recalcul des statistiques de {len(touched_stats)} noms…")
        for i, name in enumerate(sorted(touched_stats), 1):
            mox_to_stats.project_commander(name, session)
            if i % 100 == 0:
                print(f"   {i}/{len(touched_stats)}")
        n = mox_to_stats.recompute_global_stats(session)
        print(f"   deck_stat_global : {n} cartes distinctes.")
    finally:
        session.close()
    print("\nTerminé. Relancer « Réentraîner » dans l'administration pour les regroupements.")


if __name__ == "__main__":
    main()
