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
from sqlalchemy.engine import Connection

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


# ── Périmètre d'Alembic ────────────────────────────────────────────────────────
# La base est partagée avec deux autres producteurs : le catalogue Magic
# (scryfall_*, cardmarket_*, import_runs), dont le schéma appartient au dépôt
# MTG-DB, et RELIC-Trade (orders, sessions, card_pricing_rules…). Ni l'un ni
# l'autre ne doit apparaître dans une migration ManaMind.
#
# Le filtrage est une liste blanche, pas une liste noire : une table inconnue
# est ignorée par défaut. Une liste noire laisserait passer toute table ajoutée
# plus tard par un autre projet, et l'autogenerate proposerait de la supprimer.
MANAMIND_TABLES = frozenset({
    # Comptes et données saisies par l'utilisateur
    "users",
    "user_profiles",
    "user_collection",
    "user_deck_cards",
    "user_deck_tokens",
    "user_preferred_printings",
    "user_hidden_moves",
    "user_moxfield_decks",
    "user_opened_sets",
    "invitations",
    # Decks scrapés et leur progression d'import
    "deck_cards",
    "deck_cards_import_progress",
    # Statistiques et regroupements produits par les scripts ManaMind
    "deck_stat_commander_curve",
    "commanders",
    # (card_min_price est une vue matérialisée : hors du champ d'Alembic)
    "commander_clusters",
    "commander_cluster_meta",
    "commander_cluster_progress",
    "card_neighbors",
    "card_tag_clusters",
    "card_clusters_global",
    "tag_cluster_probabilities",
})

# deck_stat_commander et deck_stat_global sont volontairement absentes : leur
# schéma est déclaré dans le paquet mtgdb, mais ManaMind y ajoute ses propres
# colonnes (tfidf, idf, tfidf_norm). L'autogenerate, qui ne voit que le modèle
# mtgdb, proposerait de supprimer ces colonnes. Leurs migrations s'écrivent à
# la main ; include_object ne gêne que la détection automatique, pas
# l'exécution d'une migration déjà écrite.


def include_object(object: object, name: str, type_: str, reflected: bool, compare_to: object) -> bool:  # noqa: A002
    """N'expose à l'autogenerate que les tables dont ManaMind est propriétaire."""
    if type_ == "table":
        if not isinstance(name, str) or name not in MANAMIND_TABLES:
            return False
        # ManaMind écrit ses migrations à la main : ses tables n'ont pas de
        # modèle SQLAlchemy, donc target_metadata les ignore. Sans ce test,
        # l'autogenerate les verrait comme « supprimées » et produirait un
        # drop_table sur users, user_collection et le reste.
        if reflected and name not in target_metadata.tables:
            return False
        return True
    # Index, contraintes et colonnes suivent le sort de leur table.
    table_name = getattr(object, "table", None)
    if table_name is not None:
        return getattr(table_name, "name", None) in MANAMIND_TABLES
    return True


# ── Garde-fou : ne jamais migrer la base partagée de production ────────────────
# Un .env mal pointé suffirait à lancer une migration ManaMind sur la base
# Render qui héberge le catalogue et RELIC. On refuse de démarrer si on y
# reconnaît des tables RELIC, que ManaMind ne possède pas et ne doit pas voir.
_RELIC_MARKERS = ("orders", "card_pricing_rules", "global_settings")


def assert_not_shared_database(connection: Connection) -> None:
    """Interrompt la migration si la connexion vise la base partagée RELIC."""
    if os.getenv("ALEMBIC_ALLOW_SHARED_DB") == "1":
        return
    from sqlalchemy import text as _text

    found = connection.execute(
        _text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(:names)"
        ),
        {"names": list(_RELIC_MARKERS)},
    ).scalars().all()
    if found:
        raise RuntimeError(
            "Migration interrompue : la base visée contient des tables RELIC "
            f"({', '.join(sorted(found))}). ManaMind ne migre jamais la base "
            "partagée de production — vérifiez DATABASE_URL dans .env."
        )

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
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Mode online (connexion directe à la base) ──────────────────────────────────
def run_migrations_online() -> None:
    from sqlalchemy import create_engine
    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        assert_not_shared_database(connection)
        # Le garde-fou interroge information_schema, et ce SELECT ouvre une
        # transaction implicite. Alembic, voyant la connexion deja engagee,
        # renonce alors a ouvrir la sienne — et ne la valide donc jamais : la
        # migration s'executait, `alembic upgrade head` sortait en code 0, puis
        # SQLAlchemy annulait tout en fermant la connexion. Ni la table creee ni
        # la ligne d'alembic_version ne survivaient, sans le moindre message.
        # Ce rollback ferme la transaction de lecture et rend la main a Alembic.
        connection.rollback()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
