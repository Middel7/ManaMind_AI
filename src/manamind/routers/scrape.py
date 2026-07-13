"""Router admin — lancement du scraper Moxfield en tâche de fond."""
from __future__ import annotations

import os
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text

from manamind.auth import require_admin
from manamind.routers._shared import _json_response

router = APIRouter()

# Stockage en mémoire des jobs de scrape (process unique, suffit pour usage admin)
_JOBS: dict[str, dict] = {}


def _mox_engine():
    """Crée et retourne un engine pour les tables mox_* (avec init_schema idempotent)."""
    from manamind.moxfield_scraper import init_schema, make_engine
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL non défini")
    engine = make_engine(db_url)
    init_schema(engine)
    return engine


# ── Scrape décks récents ─────────────────────────────────────────────────────

def _run_scrape(job_id: str, limit: int) -> None:
    """Exécuté dans un thread daemon séparé pour ne pas bloquer l'event loop."""
    try:
        from manamind.moxfield_scraper import scrape

        engine = _mox_engine()
        logs: list[str] = _JOBS[job_id]["log"]

        def log_fn(msg: str) -> None:
            logs.append(msg)

        stats = scrape(engine, limit=limit, skip_known=False, log=log_fn)

        _JOBS[job_id].update(
            status="done",
            stats={
                "seen":       stats.seen,
                "skipped":    stats.skipped,
                "fetched":    stats.fetched,
                "incomplete": stats.incomplete,
                "created":    stats.created,
                "updated":    stats.updated,
            },
        )
    except Exception as exc:
        _JOBS[job_id].update(status="error", error=str(exc))


@router.post("/api/admin/scrape/recent")
def start_scrape_recent(
    limit: int = Query(default=300, ge=10, le=2000),
    _user: dict = Depends(require_admin),
):
    """Lance un scrape « decks récents » en arrière-plan. Retourne un job_id."""
    job_id = str(uuid.uuid4())
    _JOBS[job_id] = {"status": "running", "log": [], "stats": None, "error": None}
    t = threading.Thread(target=_run_scrape, args=(job_id, limit), daemon=True)
    t.start()
    return _json_response({"job_id": job_id})


# ── Scrape par commandant (les plus anciens en priorité) ─────────────────────

def _run_scrape_stale(job_id: str, count: int, limit_per: int) -> None:
    """Scrape les `count` commandants dont last_scraped_at est le plus ancien (NULL en premier)."""
    try:
        from manamind.moxfield_scraper import scrape
        from manamind.moxfield_scraper.db import commanders as cmd_table, mark_commander_scraped

        engine = _mox_engine()
        logs: list[str] = _JOBS[job_id]["log"]

        def log_fn(msg: str) -> None:
            logs.append(msg)

        with engine.connect() as conn:
            rows = conn.execute(
                select(cmd_table.c.name)
                .order_by(text("last_scraped_at ASC NULLS FIRST"), cmd_table.c.rank.asc())
                .limit(count)
            ).fetchall()

        targets = [r[0] for r in rows]
        if not targets:
            _JOBS[job_id].update(status="done", stats={
                "commanders_processed": 0, "seen": 0, "fetched": 0,
                "incomplete": 0, "created": 0, "updated": 0,
            })
            return

        total = {"seen": 0, "fetched": 0, "incomplete": 0, "created": 0, "updated": 0}

        for i, name in enumerate(targets, 1):
            log_fn(f"[{i}/{len(targets)}] {name}")
            try:
                stats = scrape(engine, limit=limit_per, commander=name, skip_known=False, log=log_fn)
                total["seen"]       += stats.seen
                total["fetched"]    += stats.fetched
                total["incomplete"] += stats.incomplete
                total["created"]    += stats.created
                total["updated"]    += stats.updated
                mark_commander_scraped(engine, name, stats.saved)
            except Exception as exc:
                log_fn(f"  ERREUR: {exc}")

        _JOBS[job_id].update(
            status="done",
            stats={"commanders_processed": len(targets), **total},
        )
    except Exception as exc:
        _JOBS[job_id].update(status="error", error=str(exc))


@router.post("/api/admin/scrape/stale")
def start_scrape_stale(
    count: int = Query(default=1, ge=1, le=100),
    limit_per: int = Query(default=200, ge=10, le=1000),
    _user: dict = Depends(require_admin),
):
    """Lance le scrape des `count` commandants dont le dernier scrape est le plus ancien."""
    job_id = str(uuid.uuid4())
    _JOBS[job_id] = {"status": "running", "log": [], "stats": None, "error": None}
    t = threading.Thread(target=_run_scrape_stale, args=(job_id, count, limit_per), daemon=True)
    t.start()
    return _json_response({"job_id": job_id})


# ── Statut d'un job ──────────────────────────────────────────────────────────

@router.get("/api/admin/scrape/status/{job_id}")
def get_scrape_status(
    job_id: str,
    _user: dict = Depends(require_admin),
):
    """Retourne l'état courant d'un job de scrape."""
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job inconnu")
    return _json_response(job)


# ── Stats dashboard ──────────────────────────────────────────────────────────

@router.get("/api/admin/stats")
def get_admin_stats(_user: dict = Depends(require_admin)):
    """Compteurs globaux pour le dashboard admin."""
    try:
        engine = _mox_engine()
        with engine.connect() as conn:
            total_decks      = conn.execute(text("SELECT COUNT(*) FROM mox_decks")).scalar() or 0
            total_commanders = conn.execute(text("SELECT COUNT(*) FROM mox_commanders")).scalar() or 0
            pending          = conn.execute(text(
                "SELECT COUNT(*) FROM mox_commanders WHERE last_scraped_at IS NULL"
            )).scalar() or 0
            unique_in_decks  = conn.execute(text(
                "SELECT COUNT(DISTINCT commander) FROM mox_decks WHERE commander IS NOT NULL"
            )).scalar() or 0
    except Exception:
        total_decks = total_commanders = pending = unique_in_decks = 0

    return _json_response({
        "total_decks":              total_decks,
        "total_commanders":         total_commanders,
        "pending_commanders":       pending,
        "unique_commanders_in_decks": unique_in_decks,
    })


# ── Liste des commandants ────────────────────────────────────────────────────

@router.get("/api/admin/commanders")
def list_commanders(_user: dict = Depends(require_admin)):
    """Liste de tous les commandants avec leur statut de scraping."""
    try:
        engine = _mox_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT
                    mc.name,
                    mc.rank,
                    mc.color_identity,
                    mc.decks_extracted,
                    mc.last_scraped_at,
                    COALESCE(d.cnt, 0) AS decks_in_db
                FROM mox_commanders mc
                LEFT JOIN (
                    SELECT commander, COUNT(*) AS cnt
                    FROM mox_decks
                    WHERE commander IS NOT NULL
                    GROUP BY commander
                ) d ON d.commander = mc.name
                ORDER BY
                    last_scraped_at ASC NULLS FIRST,
                    mc.rank ASC NULLS LAST
            """)).fetchall()

        commanders = [
            {
                "name":           r.name,
                "rank":           r.rank,
                "color_identity": r.color_identity or "",
                "decks_extracted": r.decks_extracted,
                "last_scraped_at": r.last_scraped_at.isoformat() if r.last_scraped_at else None,
                "decks_in_db":    r.decks_in_db,
            }
            for r in rows
        ]
    except Exception:
        commanders = []

    return _json_response({"commanders": commanders})


@router.post("/api/admin/commanders/sync")
def sync_commanders(_user: dict = Depends(require_admin)):
    """Ajoute dans mox_commanders les commandants uniques de mox_decks non encore référencés."""
    try:
        engine = _mox_engine()
        with engine.begin() as conn:
            result = conn.execute(text("""
                INSERT INTO mox_commanders (name)
                SELECT DISTINCT commander
                FROM mox_decks
                WHERE commander IS NOT NULL
                ON CONFLICT (name) DO NOTHING
            """))
            added = result.rowcount
        return _json_response({"added": added})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
