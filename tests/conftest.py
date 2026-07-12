"""Configuration pytest pour ManaMind AI."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

# Permettre l'import depuis la racine
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Variables d'environnement de test — à définir AVANT tout import applicatif
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-tests-only-not-prod")
os.environ.setdefault("DEBUG", "true")
# Empêcher le lifespan d'appeler alembic upgrade head (pas de vraie DB en test)
os.environ.setdefault("SKIP_ALEMBIC", "true")


def _noop_lifespan(app):
    """Lifespan minimal qui ne lance ni alembic ni le pré-chargement IA."""
    @asynccontextmanager
    async def _inner(app):
        yield
    return _inner(app)


@pytest.fixture(scope="session")
def client():
    """
    TestClient FastAPI synchrone.

    Le lifespan de server.py est patché pour éviter :
      - l'appel à ``alembic upgrade head`` (nécessite une vraie DB PostgreSQL)
      - le pré-chargement du DeckImprovementEngine (plusieurs Go de modèles)

    Les tests qui testent uniquement le comportement HTTP (auth, sécurité) n'ont
    pas besoin d'une vraie DB — la plupart des routes renvoient 401/403 avant
    toute requête SQL.
    """
    from fastapi.testclient import TestClient

    # Patch du lifespan uniquement — on importe server APRÈS avoir posé le patch
    # pour que l'objet `app` soit créé avec le bon lifespan.
    # server.py crée `app = FastAPI(lifespan=lifespan)` au niveau module,
    # donc on patche `server.lifespan` puis on recrée l'app via include_router
    # n'est pas possible proprement. On utilise à la place app.router.lifespan_context.
    import server as _server_module

    # Remplacer le lifespan sur l'app déjà instanciée
    _server_module.app.router.lifespan_context = _noop_lifespan

    with TestClient(_server_module.app, raise_server_exceptions=False) as c:
        yield c
