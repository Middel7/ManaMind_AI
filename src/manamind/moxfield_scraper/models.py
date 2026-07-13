"""Structures de données du domaine — aucune dépendance à Moxfield ni à la base."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Card:
    name: str
    quantity: int
    is_commander: bool


@dataclass(slots=True)
class Deck:
    deck_id: str
    commander: str | None
    deck_type: str = ""                    # "CEDH", "BUDGET" ou ""
    date_created: datetime | None = None
    date_modified: datetime | None = None  # sert de sentinelle pour l'update incrémental
    bracket: int | None = None             # 1..5
    price: Decimal | None = None
    currency: str = ""                     # "$", "€", "£"
    cards: list[Card] = field(default_factory=list)

    @property
    def card_count(self) -> int:
        return sum(c.quantity for c in self.cards)
