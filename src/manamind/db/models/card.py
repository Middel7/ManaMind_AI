# Re-export depuis le package partagé mtgdb
from mtgdb.db.models.card import Card, normalize_card_name

__all__ = ["Card", "normalize_card_name"]
