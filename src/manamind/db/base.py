"""
Classe de base SQLAlchemy partagée par tous les modèles.
Tous les modèles héritent de Base pour que Alembic puisse
détecter automatiquement les tables lors de l'autogenerate.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
