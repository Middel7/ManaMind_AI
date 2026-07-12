#!/usr/bin/env python3
"""Migre Opened.txt vers la table user_opened_sets pour l'utilisateur admin (id=1).

TODO: supprimer après migration — le fichier Opened.txt peut être retiré une fois ce script exécuté.
"""
from pathlib import Path
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))

from manamind.db.engine import SessionLocal

OPENED_FILE = ROOT / "Opened.txt"


def main() -> None:
    if not OPENED_FILE.exists():
        print("Fichier Opened.txt introuvable.")
        return
    codes = [l.strip() for l in OPENED_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not codes:
        print("Fichier Opened.txt vide, rien à migrer.")
        return
    with SessionLocal() as s:
        for code in codes:
            s.execute(text("""
                INSERT INTO user_opened_sets (user_id, set_code)
                VALUES (1, :code)
                ON CONFLICT DO NOTHING
            """), {"code": code})
        s.commit()
    print(f"Migré {len(codes)} codes de sets pour l'admin (user_id=1) : {', '.join(codes)}")


if __name__ == "__main__":
    main()
