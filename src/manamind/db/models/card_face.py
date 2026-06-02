"""
Table card_faces — Faces des cartes double-face (DFC), split, adventure, etc.
Exemples : Delver of Secrets // Insectile Aberration, Fire // Ice, Murderous Rider // Swift End.
Seulement créées si le JSON Scryfall contient un tableau "card_faces".
Les faces sont supprimées et réinsérées à chaque import (pas de clé stable côté Scryfall).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.manamind.db.base import Base

if TYPE_CHECKING:
    from src.manamind.db.models.card import Card


class CardFace(Base):
    __tablename__ = "card_faces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # FK vers la carte logique parente
    card_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Nom de cette face (ex: "Delver of Secrets")
    face_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Coût de mana propre à cette face (peut être null sur la face verso)
    mana_cost: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Ligne de type de cette face
    type_line: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Texte des règles de cette face
    oracle_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Stats de créature de cette face
    power: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    toughness: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    loyalty: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    defense: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Couleurs propres à cette face
    colors: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)

    # URLs d'images Scryfall pour cette face (plusieurs résolutions)
    image_small: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_normal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_large: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relation vers la carte parente
    card: Mapped["Card"] = relationship("Card", back_populates="faces")

    def __repr__(self) -> str:
        return f"<CardFace card_id={self.card_id} face_name={self.face_name!r}>"
