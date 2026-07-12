# Re-export depuis le package partagé mtgdb, avec pool de connexions renforcé
from mtgdb.db.engine import DATABASE_URL, check_connection, get_db

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Si une URL DB est disponible, recréer l'engine avec des paramètres de pool explicites.
# Le package mtgdb crée l'engine sans pool_size/max_overflow/pool_recycle, ce qui peut
# provoquer des fuites de connexions sous charge. On le surcharge ici.
if DATABASE_URL:
    _is_sqlite = DATABASE_URL.startswith("sqlite")
    _engine_kwargs: dict = {"echo": False}
    if not _is_sqlite:
        _engine_kwargs.update({
            "pool_size": 5,
            "max_overflow": 10,
            "pool_timeout": 30,
            "pool_pre_ping": True,
            "pool_recycle": 1800,
        })
    engine = create_engine(DATABASE_URL, **_engine_kwargs)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
else:
    engine = None
    SessionLocal = None  # type: ignore[assignment]

__all__ = ["DATABASE_URL", "SessionLocal", "check_connection", "engine", "get_db"]
