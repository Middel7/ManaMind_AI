"""
Table import_runs — Traçabilité de chaque import Scryfall.
Permet de savoir quand le dernier import a eu lieu, combien de cartes ont été importées,
et si des erreurs se sont produites. Table standalone, aucune FK sortante.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.manamind.db.base import Base


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Source des données : "scryfall" (et à terme "mtgjson", etc.)
    source: Mapped[str] = mapped_column(String(50), nullable=False)

    # URL ou chemin du fichier téléchargé (ex: URL du bulk data Scryfall)
    source_file: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Date de mise à jour du fichier source (champ "updated_at" de l'API Scryfall)
    source_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Horodatages de début et fin d'import
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Statut : "running" → "success" ou "failed"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")

    # Compteurs pour le monitoring
    cards_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    printings_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Message d'erreur complet si status="failed" (ou résumé des erreurs partielles)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ImportRun id={self.id} source={self.source!r} "
            f"status={self.status!r} cards={self.cards_imported}>"
        )
