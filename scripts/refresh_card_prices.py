#!/usr/bin/env python3
"""Recalcule le prix de reference de chaque carte.

Le prix affiche dans l'application est le low_price Cardmarket de l'edition la
moins chere, precalcule par la vue materialisee card_min_price. Cette vue ne se
met pas a jour toute seule : lancer ce script apres chaque import de prix.

    .venv\Scripts\python.exe scripts/refresh_card_prices.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import text  # noqa: E402

from manamind.db.engine import SessionLocal  # noqa: E402


def main() -> int:
    started = time.time()
    with SessionLocal() as session:
        # CONCURRENTLY : les lectures continuent pendant le rafraichissement.
        session.execute(text("COMMIT"))
        session.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY card_min_price"))
        session.commit()
        total = session.execute(text("SELECT count(*) FROM card_min_price")).scalar()
    print(f"card_min_price : {total} cartes cotees, en {time.time() - started:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
