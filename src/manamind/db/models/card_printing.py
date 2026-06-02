"""
Table card_printings — Impression physique précise d'une carte.
Un oracle_id peut avoir des dizaines de printings (reprints, collector boosters, etc.).
Clé métier : scryfall_id (UUID unique par impression dans Scryfall).
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.manamind.db.base import Base

if TYPE_CHECKING:
    from src.manamind.db.models.card import Card
    from src.manamind.db.models.card_price import CardPrice
    from src.manamind.db.models.mtg_set import MtgSet


class CardPrinting(Base):
    __tablename__ = "card_printings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # UUID Scryfall de cette impression précise (stable, unique)
    scryfall_id: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )

    # oracle_id dupliqué ici pour faciliter les requêtes sans JOIN sur cards
    oracle_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # FK vers la carte logique
    card_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # FK vers l'édition (nullable : si le set n'est pas encore importé)
    set_code: Mapped[Optional[str]] = mapped_column(
        String(16),
        ForeignKey("mtg_sets.code", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Numéro de collection (ex: "023", "023★" pour les variantes)
    collector_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Langue (ex: "en", "fr", "ja")
    lang: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Rareté : "common", "uncommon", "rare", "mythic", "special", "bonus"
    rarity: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    released_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    artist: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Informations de présentation physique
    border_color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    frame: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Flags booléens Scryfall
    full_art: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    promo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reprint: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    digital: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # URLs d'images pour cette impression précise
    image_small: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_normal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_large: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # URL de la page Scryfall de cette carte
    scryfall_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relations
    card: Mapped["Card"] = relationship("Card", back_populates="printings")
    mtg_set: Mapped[Optional["MtgSet"]] = relationship("MtgSet", back_populates="printings")
    prices: Mapped[List["CardPrice"]] = relationship(
        "CardPrice", back_populates="printing", cascade="all, delete-orphan", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<CardPrinting scryfall_id={self.scryfall_id!r} set={self.set_code!r}>"
