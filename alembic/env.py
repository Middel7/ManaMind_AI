"""
Configuration Alembic pour ManaMind AI.
- Charge DATABASE_URL depuis .env
- Importe tous les modèles pour l'autogenerate
- Supporte les migrations online (base connectée) et offline (SQL brut)
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# ── Chemins ────────────────────────────────────────────────────────────────────
# Ajoute la racine du projet au PYTHONPATH pour que les imports src.* fonctionnent
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── Variables d'environnement ───────────────────────────────────────────────────
load_dotenv(ROOT / ".env")

# ── Modèles ────────────────────────────────────────────────────────────────────
# On importe Base et tous les modèles AVANT d'utiliser target_metadata.
# Sans ces imports, Alembic ne peut pas détecter les tables lors de l'autogenerate.
from src.manamind.db.base import Base  # noqa: E402
import src.manamind.db.models  # noqa: E402, F401 — déclenche l'enregistrement des modèles

# ── Config Alembic ──────────────────────────────────────────────────────────────
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Métadonnées de toutes les tables → Alembic les compare à la base réelle
target_metadata = Base.metadata

# Injecte DATABASE_URL depuis .env (priorité sur alembic.ini)
database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)


# ── Mode offline (génère du SQL sans connexion réelle) ─────────────────────────
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Compare les types de colonnes pour détecter les changements
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Mode online (connexion directe à la base) ──────────────────────────────────
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # NullPool : pas de connexions persistantes pendant les migrations
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
