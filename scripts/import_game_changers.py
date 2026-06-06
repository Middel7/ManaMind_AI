#!/usr/bin/env python3
"""
import_game_changers.py
Importe la liste des cartes "Game Changer" depuis l'API Scryfall
et met à jour le champ `game_changer` dans la table `cards`.

Usage :
    python scripts/import_game_changers.py
    python scripts/import_game_changers.py --dry-run   # afficher sans modifier la DB
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))


def fetch_game_changers() -> list[str]:
    """
    Récupère tous les noms de cartes Game Changer via l'API Scryfall.
    Gère la pagination automatiquement.
    """
    import httpx

    names: list[str] = []
    url = "https://api.scryfall.com/cards/search?q=is:gamechanger&order=name"

    print("[Scryfall] Téléchargement de la liste Game Changer...")
    page = 1

    while url:
        print(f"[Scryfall]   Page {page}...")
        try:
            resp = httpx.get(url, timeout=30, headers={"User-Agent": "ManaMind/1.0"})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"[ERREUR] Impossible de contacter Scryfall : {exc}")
            sys.exit(1)

        for card in data.get("data", []):
            names.append(card["name"])

        if data.get("has_more") and data.get("next_page"):
            url = data["next_page"]
            page += 1
            time.sleep(0.1)  # respecter le rate limit Scryfall
        else:
            url = None

    print(f"[Scryfall] {len(names)} cartes Game Changer trouvées.")
    return names


def update_database(game_changer_names: list[str], dry_run: bool = False) -> None:
    """
    Met à jour game_changer=true pour les cartes trouvées,
    et game_changer=false pour toutes les autres.
    """
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")

    import os
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("[ERREUR] DATABASE_URL absent du .env")
        sys.exit(1)

    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)

    # Normaliser les noms pour le matching (les noms Scryfall peuvent avoir des variantes)
    import unicodedata, re

    def normalize(name: str) -> str:
        name = unicodedata.normalize("NFKD", name)
        name = "".join(c for c in name if not unicodedata.combining(c))
        return name.strip().lower()

    names_normalized = [normalize(n) for n in game_changer_names]

    if dry_run:
        print("\n[DRY-RUN] Cartes qui seraient marquées game_changer=true :")
        for name in sorted(game_changer_names):
            print(f"  - {name}")
        return

    with engine.begin() as conn:
        # Remettre tous à false d'abord
        result = conn.execute(text("UPDATE cards SET game_changer = false"))
        print(f"[DB] Reset game_changer=false sur {result.rowcount} cartes.")

        # Marquer les game changers à true — matching par name exact
        updated = 0
        not_found = []

        for name in game_changer_names:
            r = conn.execute(
                text("UPDATE cards SET game_changer = true WHERE name = :n"),
                {"n": name},
            )
            if r.rowcount > 0:
                updated += r.rowcount
            else:
                # Essai avec normalized_name
                norm = normalize(name)
                r2 = conn.execute(
                    text("UPDATE cards SET game_changer = true WHERE normalized_name = :n"),
                    {"n": norm},
                )
                if r2.rowcount > 0:
                    updated += r2.rowcount
                else:
                    not_found.append(name)

        print(f"[DB] {updated} cartes marquées game_changer=true.")

        if not_found:
            print(f"[AVERTISSEMENT] {len(not_found)} cartes non trouvées en base :")
            for n in not_found:
                print(f"  - {n}")

        # Vérification finale
        total = conn.execute(
            text("SELECT COUNT(*) FROM cards WHERE game_changer = true")
        ).scalar()
        print(f"[DB] Total game_changer=true en base : {total}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importe les cartes Game Changer depuis Scryfall"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche les cartes sans modifier la base de données",
    )
    args = parser.parse_args()

    names = fetch_game_changers()

    if not names:
        print("[ERREUR] Aucune carte récupérée depuis Scryfall.")
        sys.exit(1)

    update_database(names, dry_run=args.dry_run)

    if not args.dry_run:
        print("\n[OK] Import terminé.")
        print("     Relance le serveur pour que les changements soient pris en compte.")


if __name__ == "__main__":
    main()
