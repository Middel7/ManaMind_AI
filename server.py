#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import threading as _threading
from pathlib import Path

import re

from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

import logging
import os as _os_server
import traceback as _traceback_mod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("manamind")

# Sentry — monitoring des erreurs (optionnel, activé si SENTRY_DSN est défini)
_SENTRY_DSN = _os_server.environ.get("SENTRY_DSN", "")
if _SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        integrations=[FastApiIntegration()],
        traces_sample_rate=0.1,
        environment=_os_server.environ.get("ENVIRONMENT", "production"),
    )

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
import json as _json

_DEBUG = _os_server.environ.get("DEBUG", "").lower() in ("1", "true")
_SECURE_COOKIE = _os_server.environ.get("HTTPS_ENABLED", "").lower() in ("1", "true", "yes")

def _json_response(data: dict, status_code: int = 200) -> Response:
    """JSONResponse avec support UTF-8 complet (pas d'échappement ASCII)."""
    return Response(
        content=_json.dumps(data, ensure_ascii=False),
        status_code=status_code,
        media_type="application/json; charset=utf-8",
    )
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

ROOT = Path(__file__).resolve().parent
UPLOADS_DIR = ROOT / "uploads"
OUTPUTS_DIR = ROOT / "outputs"
OUTPUTS_RECO_DIR = ROOT / "outputs" / "recommendations"
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)
OUTPUTS_RECO_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT / "src"))

# Le limiter est défini dans _shared.py pour être partagé avec les routers
# (importé APRÈS sys.path.insert pour que le module manamind soit trouvable)
from manamind.routers._shared import limiter

def _extract_commander_from_deck(content: str) -> str | None:
    """Extrait le nom du commandant depuis le contenu texte d'une decklist."""
    lines = content.splitlines()
    sections: list[list[str]] = []
    cur: list[str] = []
    for line in lines:
        t = line.strip()
        if not t:
            if cur:
                sections.append(cur)
                cur = []
        else:
            cur.append(t)
    if cur:
        sections.append(cur)
    # Le commandant est la dernière section à une seule ligne : "1 Nom Du Commandant"
    if len(sections) >= 2:
        last = sections[-1]
        if len(last) == 1:
            m = re.match(r"^\d+\s+(.+)$", last[0])
            if m:
                return m.group(1).strip()
    return None

# ── Base de données (optionnelle : si .env absent, les routes DB retournent 503) ──
sys.path.insert(0, str(ROOT))
try:
    from sqlalchemy import func, select, text
    from sqlalchemy.orm import aliased

    from src.manamind.db.engine import SessionLocal
    from src.manamind.db.models.card import Card
    from src.manamind.db.models.card_price import CardPrice
    from src.manamind.db.models.card_printing import CardPrinting

    _DB_AVAILABLE = SessionLocal is not None
except Exception:
    SessionLocal = None  # type: ignore[assignment]
    Card = None          # type: ignore[assignment]
    CardPrinting = None  # type: ignore[assignment]
    _DB_AVAILABLE = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 1. Migrations Alembic avec timeout ──────────────────────────────────
    try:
        alembic_bin = ROOT / ".venv" / "Scripts" / "alembic.exe"
        if not alembic_bin.exists():
            alembic_bin = ROOT / ".venv" / "bin" / "alembic"
        result = subprocess.run(
            [str(alembic_bin), "upgrade", "head"],
            capture_output=True, text=True, cwd=str(ROOT),
            timeout=120,  # 2 min max — si plus long, c'est une migration problématique
        )
        if result.returncode != 0:
            # Fail-hard : une migration ratée = données incohérentes = ne pas démarrer
            raise RuntimeError(
                f"Alembic upgrade head échoué (code {result.returncode}):\n{result.stderr[:500]}"
            )
        logger.info("Migrations Alembic appliquées")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Migration Alembic trop longue (>120s) — démarrage annulé")
    except RuntimeError:
        raise
    except Exception as e:
        logger.warning(f"Impossible de lancer alembic : {e}")

    # ── 2. Pré-chargement du moteur IA en arrière-plan ──────────────────────
    import anyio
    async def _preload_engine():
        try:
            await anyio.to_thread.run_sync(_get_deck_engine)
            print("[OK] Moteur IA pré-chargé")
        except Exception as exc:
            print(f"[WARN] Pré-chargement moteur IA échoué : {exc}")
    import asyncio as _asyncio
    _asyncio.ensure_future(_preload_engine())

    yield

app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ─────────────────────────────────────────────────────────────────────
_ALLOWED_ORIGINS = [o.strip() for o in _os_server.environ.get("CORS_ORIGINS", "http://localhost:8080,http://localhost:3000").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        if _SECURE_COOKIE:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# ── Deck Improvement Engine — singleton préchargé au démarrage ───────────────
# Lock global initialisé au niveau module (thread-safe, évite la race condition)
_deck_engine = None
_deck_engine_lock = _threading.Lock()

def _get_deck_engine():
    """Retourne le DeckImprovementEngine (préchargé au démarrage, thread-safe)."""
    global _deck_engine
    with _deck_engine_lock:
        if _deck_engine is None:
            import importlib.util, sys as _sys
            script_path = ROOT / "scripts" / "deck_improver.py"
            spec = importlib.util.spec_from_file_location("deck_improver", script_path)
            mod  = importlib.util.module_from_spec(spec)
            _sys.modules["deck_improver"] = mod
            spec.loader.exec_module(mod)
            _deck_engine = mod.DeckImprovementEngine()
    return _deck_engine


# ── Routers ───────────────────────────────────────────────────────────────────
from manamind.routers.auth import router as auth_router
from manamind.routers.collection import router as collection_router
from manamind.routers.collection_v2 import router as collection_v2_router
from manamind.routers.commander_swap import router as commander_swap_router
from manamind.routers.dashboard import router as dashboard_router
from manamind.routers.decks import router as decks_router
from manamind.routers.decks_v2 import router as decks_v2_router
from manamind.routers.engine import router as engine_router
from manamind.routers.import_deck import router as import_router
from manamind.routers.pages import router as pages_router
from manamind.routers.scrape import router as scrape_router

app.include_router(auth_router)
app.include_router(collection_router)
app.include_router(collection_v2_router)
app.include_router(commander_swap_router)
app.include_router(dashboard_router)
app.include_router(decks_router)
app.include_router(decks_v2_router)
app.include_router(engine_router)
app.include_router(import_router)
app.include_router(pages_router)
app.include_router(scrape_router)


# ── Route racine ─────────────────────────────────────────────────────────────
@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        ROOT / "dashboard.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ── Version deployee ──────────────────────────────────────────────────────────
# Calculee une fois : appeler git a chaque requete couterait plus cher que la
# reponse elle-meme. Le numero est le nombre de commits, qui ne fait que croitre
# et se compare donc d'un coup d'oeil.
def _read_version() -> dict:
    import subprocess
    def _git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                                  text=True, timeout=5).stdout.strip()
        except Exception:
            return ""
    count = _git("rev-list", "--count", "HEAD")
    return {
        "build": int(count) if count.isdigit() else 0,
        "sha": _git("rev-parse", "--short", "HEAD"),
        "date": _git("log", "-1", "--format=%cI"),
        "subject": _git("log", "-1", "--format=%s")[:120],
    }


_VERSION = _read_version()


@app.get("/api/version")
def api_version() -> Response:
    """Version deployee, pour savoir d'un coup d'oeil si l'on est a jour."""
    return _json_response(_VERSION)


@app.get("/health")
def health_check() -> JSONResponse:
    from sqlalchemy import text as _health_text
    status: dict = {"status": "ok", "db": "ok", "engine": "not_loaded"}
    http_status = 200

    try:
        if SessionLocal is not None:
            with SessionLocal() as s:
                s.execute(_health_text("SELECT 1"))
        else:
            status["db"] = "unavailable"
            status["status"] = "degraded"
            http_status = 503
    except Exception as e:
        status["db"] = f"error: {str(e)[:100]}"
        status["status"] = "degraded"
        http_status = 503

    if _deck_engine is not None:
        status["engine"] = "ready"

    return _json_response(status, status_code=http_status)


# ── Fichiers statiques ────────────────────────────────────────────────────────

class RevalidatedStaticFiles(StaticFiles):
    """Statiques revalidées à chaque chargement.

    Sans en-tête de cache explicite, le navigateur applique une heuristique et
    peut servir un mm.js périmé pendant des heures : une modification du front
    passe alors inaperçue, avec des erreurs incompréhensibles à la clé (une
    page à jour appelant un script qui ne l'est pas). « no-cache » n'interdit
    pas la mise en cache, il impose la revalidation — Starlette répond 304 tant
    que le fichier n'a pas changé, donc le coût est une requête vide.
    """

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static",  RevalidatedStaticFiles(directory=str(ROOT / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")
app.mount("/data",    StaticFiles(directory=str(ROOT / "data")), name="data")


# ── Wildcard statique — DOIT RESTER EN DERNIER ───────────────────────────────
_ALLOWED_EXTENSIONS = {".html", ".js", ".css", ".png", ".jpg", ".jpeg", ".avif", ".ico", ".svg", ".webp"}
_BLOCKED_NAMES = {".env", ".env.example", "alembic.ini", "pyproject.toml", "uv.lock"}


@app.get("/{filename:path}")
def static_file(filename: str) -> FileResponse:
    from fastapi import HTTPException
    # Bloquer les fichiers sensibles par nom
    name = Path(filename).name
    if name.startswith(".") or name in _BLOCKED_NAMES:
        raise HTTPException(status_code=404)
    # S'assurer que le chemin reste dans ROOT (pas de path traversal)
    file_path = ROOT / filename
    try:
        file_path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=404)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404)
    suffix = file_path.suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=404)
    return FileResponse(file_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(_os_server.environ.get("PORT", "8080")),
        workers=1,  # 1 seul worker : le moteur IA (~2 Go RAM) ne supporte pas la duplication
        log_level="info",
    )
