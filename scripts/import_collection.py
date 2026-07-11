#!/usr/bin/env python3
"""
import_collection.py

Importe Ma collection.txt dans la table user_collection.
Format : "2 Nom de la carte (SET) #numero [*F*]"

Usage :
    uv run python scripts/import_collection.py
    uv run python scripts/import_collection.py --reset   # vide la table avant d'importer
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import psycopg2
import os

COLLECTION_FILE = ROOT / "Ma collection.txt"
RE_LINE = re.compile(r"^(\d+)\s+(.+?)\s+\([^)]+\)\s+#\S+")


def parse_collection(path: Path) -> list[tuple[int, str, str]]:
    """Parse chaque ligne et retourne (quantity, card_name, raw_line)."""
    results = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            m = RE_LINE.match(line)
            if m:
                qty = int(m.group(1))
                name = m.group(2).strip()
                # Gérer les double-faced cards : prendre seulement le nom avant "//"
                if " // " in name:
                    name = name.split(" // ")[0].strip()
                results.append((qty, name, line))
            else:
                print(f"  [SKIP] ligne non parsée : {line!r}")
    return results


def get_conn():
    url = os.environ["DATABASE_URL"]
    url = url.replace("postgresql+psycopg2://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(url)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Vider la table avant d'importer")
    args = parser.parse_args()

    print(f"Lecture de {COLLECTION_FILE}...")
    rows = parse_collection(COLLECTION_FILE)

    # Agréger par nom de carte (somme des quantités)
    aggregated: dict[str, tuple[int, str]] = {}
    for qty, name, raw in rows:
        if name in aggregated:
            aggregated[name] = (aggregated[name][0] + qty, aggregated[name][1])
        else:
            aggregated[name] = (qty, raw)

    print(f"  {len(rows)} lignes parsees -> {len(aggregated)} cartes uniques")

    conn = get_conn()
    with conn.cursor() as cur:
        if args.reset:
            cur.execute("TRUNCATE user_collection RESTART IDENTITY")
            print("  Table vidée.")

        cur.executemany("""
            INSERT INTO user_collection (card_name, quantity, raw_line)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
        """, [(name, qty, raw) for name, (qty, raw) in aggregated.items()])

        cur.execute("SELECT COUNT(*) FROM user_collection")
        total = cur.fetchone()[0]

    conn.commit()
    conn.close()
    print(f"  Import terminé. {total} cartes dans user_collection.")


if __name__ == "__main__":
    main()
