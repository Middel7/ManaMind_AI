"""
Table card_prices — Historique des prix par impression.
Append-only : on ne met jamais à jour les prix passés, on insère une nouvelle ligne par import.
Cela permet de suivre l'évolution des prix dans le temps.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.manamind.db.base import Base

if TYPE_CHECKING:
    from src.manamind.db.models.card_printing import CardPrinting


class CardPrice(Base):
    __tablename__ = "card_prices"

    # Contrainte unique pour l'idempotence : un prix par impression / jour / devise / type
    # Permet le ON CONFLICT DO NOTHING lors des réimports
    __table_args__ = (
        UniqueConstraint(
            "printing_id", "date", "source", "currency", "price_type",
            name="uq_card_prices_printing_date_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # FK vers l'impression concernée
    printing_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("card_printings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Source du prix : "scryfall" (et à terme "cardmarket", "tcgplayer", etc.)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="scryfall")

    # Devise : "eur", "usd", "tix" (Magic Online)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Type de finition : "regular", "foil", "etched"
    price_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # Prix avec 2 décimales (null si Scryfall ne connaît pas le prix)
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)

    # Date de collecte du prix (date de l'import)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Relation
    printing: Mapped["CardPrinting"] = relationship("CardPrinting", back_populates="prices")

    def __repr__(self) -> str:
        return (
            f"<CardPrice printing_id={self.printing_id} "
            f"{self.currency} {self.price_type}={self.price}>"
        )
