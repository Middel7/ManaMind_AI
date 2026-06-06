"""
Table cards — Carte logique (niveau oracle), indépendante des éditions/reprints.
Un oracle_id = une seule entrée, peu importe le nombre d'impressions.
Source : Scryfall bulk data (champ oracle_id).
"""
from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.manamind.db.base import Base

if TYPE_CHECKING:
    from src.manamind.db.models.card_face import CardFace
    from src.manamind.db.models.card_printing import CardPrinting


def normalize_card_name(name: str) -> str:
    """
    Normalise un nom de carte pour la recherche :
    - minuscules
    - suppression des accents (NFD → ASCII)
    - suppression des espaces superflus
    - conserve le '//' des cartes split (ex: "Fire // Ice" → "fire // ice")
    """
    # NFD sépare les caractères de leur accent, on garde seulement les ASCII
    nfd = unicodedata.normalize("NFD", name)
    ascii_only = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return ascii_only.strip().lower()


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Identifiant Scryfall unique par carte logique (stable entre les reprints)
    oracle_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)

    # Nom complet (ex: "Fire // Ice" pour les cartes split)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Nom normalisé pour la recherche et le matching avec les decklists texte
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Coût de mana (ex: "{2}{U}{B}") — null pour les terrains et les faces de DFC
    mana_cost: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Coût de mana converti (CMC) — float pour gérer les X et les demi-mana
    mana_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Ligne de type complète (ex: "Legendary Creature — Human Wizard")
    type_line: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Texte des règles oracle
    oracle_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Stats de créature (stockées en str car "*/2+1" est possible)
    power: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    toughness: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Stats de planeswalker / bataille
    loyalty: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    defense: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Couleurs de la carte (ex: ["U", "B"]) — ARRAY PostgreSQL natif
    colors: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)

    # Identité de couleur pour Commander (inclut les coûts des capacités activées)
    color_identity: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)

    # Mots-clés de règles (ex: ["Flying", "Deathtouch"])
    keywords: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)

    # True si la carte est légale en format Commander
    legal_commander: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Classement EDHREC (plus petit = plus populaire ; null si absent)
    edhrec_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # True si la carte est classée "Game Changer" par Scryfall
    game_changer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")

    # Horodatages gérés automatiquement
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relations
    faces: Mapped[List["CardFace"]] = relationship(
        "CardFace", back_populates="card", cascade="all, delete-orphan", lazy="select"
    )
    printings: Mapped[List["CardPrinting"]] = relationship(
        "CardPrinting", back_populates="card", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Card oracle_id={self.oracle_id!r} name={self.name!r}>"
