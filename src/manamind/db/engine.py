"""
Connexion SQLAlchemy à PostgreSQL.
Charge DATABASE_URL depuis le fichier .env à la racine du projet.

Usage :
    from src.manamind.db.engine import SessionLocal, check_connection
    db = SessionLocal()

Note : si DATABASE_URL est absent du .env, engine et SessionLocal valent None.
       check_connection() retourne False dans ce cas.
       L'erreur est levée uniquement quand on essaie d'utiliser la session.
"""
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

# Charge le .env situé à la racine du projet
_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env")

DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL")

if DATABASE_URL:
    engine: Optional[Engine] = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
else:
    engine = None
    SessionLocal = None  # type: ignore[assignment]


def get_db():
    """Générateur de session pour FastAPI (Depends)."""
    if SessionLocal is None:
        raise RuntimeError(
            "DATABASE_URL absent du .env. "
            "Copie .env.example en .env et renseigne la valeur."
        )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_connection() -> bool:
    """Vérifie que la base est accessible. Retourne True si OK, False sinon."""
    if engine is None:
        print("DATABASE_URL absent du .env — connexion impossible.")
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        print(f"Connexion échouée : {exc}")
        return False
