# Re-exports depuis le package partagé mtgdb
# Alembic détecte les tables via Base.metadata (importé dans alembic/env.py)
from mtgdb.db.models import Card, CardFace, CardPrice, CardPrinting, ImportRun, MtgSet

__all__ = ["Card", "CardFace", "CardPrinting", "MtgSet", "CardPrice", "ImportRun"]
