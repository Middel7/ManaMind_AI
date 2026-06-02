"""
Table mtg_sets — Éditions Magic: The Gathering.
Source : Scryfall bulk data (champ `set` et objet Set de l'API).
Clé métier : code (ex: "bro", "ltr", "mkm")
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Date, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.manamind.db.base import Base

if TYPE_CHECKING:
    from src.manamind.db.models.card_printing import CardPrinting


class MtgSet(Base):
    __tablename__ = "mtg_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Code unique de l'édition (ex: "bro" pour The Brothers' War)
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Type d'édition : "expansion", "core", "commander", "masters", etc.
    set_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    released_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Bloc auquel appartient l'édition (peut être null pour les sets modernes)
    block: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Code du set parent (ex: les sets Commander pointent vers leur set principal)
    parent_set_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    card_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # URL du SVG de l'icône sur Scryfall
    icon_svg_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relation inverse : toutes les impressions de cartes de cet ensemble
    printings: Mapped[List["CardPrinting"]] = relationship(
        "CardPrinting", back_populates="mtg_set", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<MtgSet code={self.code!r} name={self.name!r}>"
