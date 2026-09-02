"""Router admin — lancement du scraper Moxfield en tâche de fond."""
from __future__ import annotations

import json
import os
import pathlib
import re
import threading
import time
import uuid
from datetime import datetime, timezone

_ML_METADATA_FILE     = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "ml_metadata.json"
_SCRAPE_METADATA_FILE = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "scrape_metadata.json"


def _read_ml_metadata() -> dict:
    try:
        return json.loads(_ML_METADATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_ml_metadata(data: dict) -> None:
    try:
        _ML_METADATA_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _read_scrape_metadata() -> dict:
    try:
        return json.loads(_SCRAPE_METADATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_scrape_metadata(data: dict) -> None:
    try:
        _SCRAPE_METADATA_FILE.write_text(json.dumps(data, default=str), encoding="utf-8")
    except Exception:
        pass


def _update_scrape_timing(mode: str, elapsed_secs: float, sample_size: int, **extra) -> None:
    """Met à jour la moyenne glissante de temps par unité (deck ou commandant)."""
    if sample_size <= 0 or elapsed_secs <= 0:
        return
    meta = _read_scrape_metadata()
    entry = meta.get(mode, {})
    key = "avg_secs_per_deck" if mode == "recent" else "avg_secs_per_commander"
    old_avg = entry.get(key, 0.0)
    old_n   = entry.get("n", 0)
    raw_avg = elapsed_secs / sample_size
    new_n   = old_n + sample_size
    new_avg = (old_avg * old_n + raw_avg * sample_size) / new_n if old_n > 0 else raw_avg
    meta[mode] = {
        key:           round(new_avg, 3),
        "n":           min(new_n, 2000),  # cap pour rester réactif aux changements
        "last_updated": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    _write_scrape_metadata(meta)

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from manamind.auth import require_admin
from manamind.routers._shared import _json_response

router = APIRouter()

_JOBS: dict[str, dict] = {}
_STOP_FLAGS: dict[str, threading.Event] = {}
_STATS_CACHE: dict = {}
_DECK_LIMIT_PER_COMMANDER = 1000

_RE_PAGE    = re.compile(r"page (\d+)/(\d+) téléchargée")
_RE_DL      = re.compile(r"Téléchargement de (\d+) decks")
_RE_COLLECT = re.compile(r"Collecte des liens")
_RE_CMD     = re.compile(r"^\[(\d+)/(\d+)\] (.+)$")
_RE_VIEWMORE = re.compile(r"view-more=\s*(\d+)\s+decks=(\d+)/(\d+)")


def _make_engine():
    from manamind.moxfield_scraper.db import make_engine
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL non défini")
    return make_engine(db_url)


def _make_log_fn(job_id: str):
    """Retourne une fonction de log qui enregistre les messages structurés
    et met à jour le bloc `progress` du job en temps réel."""
    def log_fn(msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        job = _JOBS.get(job_id)
        if job is None:
            return

        logs: list = job["log"]
        logs.append({"ts": ts, "msg": msg})
        # Limiter la taille du log en mémoire
        if len(logs) > 1000:
            del logs[:200]

        prog: dict = job["progress"]

        if _RE_COLLECT.search(msg):
            prog["phase"] = "Collecte des liens"
            prog["decks_done"] = 0
            prog["decks_total"] = 0

        elif m := _RE_DL.search(msg):
            prog["phase"] = "Téléchargement"
            prog["decks_total"] = int(m.group(1))
            prog["decks_done"] = 0

        elif m := _RE_PAGE.search(msg):
            prog["decks_done"] = int(m.group(1))
            prog["decks_total"] = int(m.group(2))

        elif m := _RE_VIEWMORE.search(msg):
            prog["phase"] = "Collecte des liens"
            prog["links_done"] = int(m.group(2))
            prog["links_total"] = int(m.group(3))

        elif m := _RE_CMD.match(msg):
            prog["commander_idx"]   = int(m.group(1))
            prog["commander_total"] = int(m.group(2))
            prog["commander"]       = m.group(3)
            prog["phase"]           = "Initialisation"
            prog["decks_done"]      = 0
            prog["decks_total"]     = 0
            prog["links_done"]      = 0
            prog["links_total"]     = 0

        elif "enregistrés" in msg:
            prog["phase"] = "Sauvegarde"

        elif "ERREUR" in msg or "échec" in msg.lower():
            prog["last_error"] = msg

    return log_fn


def _new_job() -> dict:
    return {
        "status":   "running",
        "log":      [],
        "progress": {
            "phase":            "Initialisation",
            "commander":        None,
            "commander_idx":    0,
            "commander_total":  0,
            "decks_done":       0,
            "decks_total":      0,
            "links_done":       0,
            "links_total":      0,
            "last_error":       None,
            "stopping":         False,
            "started_at":       datetime.now(timezone.utc).isoformat(),
        },
        "stats": None,
        "error": None,
    }


# ── Limite de decks par commandant ───────────────────────────────────────────

def _enforce_deck_limit(engine, log_fn=None) -> int:
    """Supprime les decks les plus anciens (date_modified) quand un commandant
    dépasse _DECK_LIMIT_PER_COMMANDER decks. Retourne le nombre de lignes supprimées."""
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                DELETE FROM deck_cards
                WHERE deck_id IN (
                    SELECT deck_id FROM (
                        SELECT deck_id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY commander
                                   ORDER BY MAX(date_modified) DESC NULLS LAST
                               ) AS rn
                        FROM deck_cards
                        WHERE commander IS NOT NULL
                        GROUP BY deck_id, commander
                    ) t
                    WHERE rn > :lim
                )
            """),
            {"lim": _DECK_LIMIT_PER_COMMANDER},
        )
        deleted = result.rowcount
    if log_fn and deleted > 0:
        log_fn(f"  Nettoyage : {deleted:,} lignes supprimées (limite {_DECK_LIMIT_PER_COMMANDER} decks/commandant)")
    return deleted


# ── Scrape décks récents ─────────────────────────────────────────────────────

def _run_scrape(job_id: str, limit: int, headless: bool) -> None:
    try:
        flag = _STOP_FLAGS.get(job_id, threading.Event())
        from manamind.moxfield_scraper import scrape
        engine = _make_engine()
        log_fn = _make_log_fn(job_id)

        t0 = time.monotonic()
        stats = scrape(engine, limit=limit, skip_known=False,
                       batch_size=100, headless=headless, log=log_fn,
                       stop_event=flag)
        elapsed = time.monotonic() - t0

        stopped = flag.is_set()
        if stats.fetched > 0 and not stopped:
            _update_scrape_timing("recent", elapsed, stats.fetched)

        deleted = _enforce_deck_limit(engine, log_fn)
        _JOBS[job_id].update(
            status="stopped" if stopped else "done",
            stats={
                "seen":               stats.seen,
                "skipped":            stats.skipped,
                "fetched":            stats.fetched,
                "incomplete":         stats.incomplete,
                "created":            stats.created,
                "updated":            stats.updated,
                "elapsed_s":          round(elapsed, 1),
                "deck_limit_deleted": deleted,
            },
        )
        threading.Thread(target=_refresh_stats_cache, daemon=True).start()
    except Exception as exc:
        _JOBS[job_id].update(status="error", error=str(exc))
    finally:
        _STOP_FLAGS.pop(job_id, None)


@router.post("/api/admin/scrape/recent")
def start_scrape_recent(
    limit: int = Query(default=300, ge=10, le=2000),
    headless: bool = Query(default=True),
    _user: dict = Depends(require_admin),
):
    job_id = str(uuid.uuid4())
    _JOBS[job_id] = _new_job()
    _STOP_FLAGS[job_id] = threading.Event()
    t = threading.Thread(target=_run_scrape, args=(job_id, limit, headless), daemon=True)
    t.start()
    return _json_response({"job_id": job_id})


# ── Scrape par commandant ────────────────────────────────────────────────────

_STALE_QUERIES: dict[str, str] = {
    # Commandants dont le scrape est le plus ancien (NULL = jamais scrapé, en priorité)
    "stale": """
        SELECT name FROM commanders
        ORDER BY last_scraped_at ASC NULLS FIRST, rank ASC NULLS LAST
        LIMIT :n
    """,
    # Commandants avec le moins de decks en base (NULL last_scraped_at en priorité absolue)
    "min_decks": """
        SELECT c.name
        FROM commanders c
        LEFT JOIN (
            SELECT commander, COUNT(DISTINCT deck_id) AS cnt
            FROM deck_cards WHERE commander IS NOT NULL GROUP BY commander
        ) d ON d.commander = c.name
        ORDER BY c.last_scraped_at ASC NULLS FIRST,
                 COALESCE(d.cnt, 0) ASC,
                 c.rank ASC NULLS LAST
        LIMIT :n
    """,
}


def _run_scrape_stale(job_id: str, count: int, limit_per: int, headless: bool, mode: str = "stale") -> None:
    try:
        flag = _STOP_FLAGS.get(job_id, threading.Event())
        from manamind.moxfield_scraper import scrape
        from manamind.moxfield_scraper.db import mark_commander_scraped

        engine = _make_engine()
        log_fn = _make_log_fn(job_id)
        t0 = time.monotonic()

        query = _STALE_QUERIES.get(mode, _STALE_QUERIES["stale"])
        with engine.connect() as conn:
            rows = conn.execute(text(query), {"n": count}).fetchall()

        targets = [r[0] for r in rows]
        if not targets:
            _JOBS[job_id].update(status="done", stats={
                "commanders_processed": 0, "seen": 0, "fetched": 0,
                "incomplete": 0, "created": 0, "updated": 0,
            })
            return

        _JOBS[job_id]["progress"]["commander_total"] = len(targets)
        total = {"seen": 0, "fetched": 0, "incomplete": 0, "created": 0, "updated": 0}
        commanders_done = 0

        for i, name in enumerate(targets, 1):
            if flag.is_set():
                log_fn("Arrêt demandé — scraping interrompu.")
                break

            log_fn(f"[{i}/{len(targets)}] {name}")

            # Thread isolé par commandant (asyncio.run + Playwright ne se réutilisent
            # pas proprement dans le même thread)
            _result: dict = {}
            _err: list = []

            def _scrape_one(_name=name, _result=_result, _err=_err) -> None:
                try:
                    _result["stats"] = scrape(
                        engine, limit=limit_per, commander=_name,
                        skip_known=False, batch_size=100,
                        headless=headless, log=log_fn,
                        stop_event=flag,
                    )
                except Exception as e:
                    _err.append(str(e))

            t = threading.Thread(target=_scrape_one, daemon=True)
            t.start()
            t.join()

            if _err:
                log_fn(f"  ERREUR: {_err[0]}")
                commanders_done += 1
                continue

            s = _result.get("stats")
            if s is None:
                commanders_done += 1
                continue

            total["seen"]       += s.seen
            total["fetched"]    += s.fetched
            total["incomplete"] += s.incomplete
            total["created"]    += s.created
            total["updated"]    += s.updated
            mark_commander_scraped(engine, name, s.saved)
            log_fn(f"  ✓ {s.saved} decks enregistrés pour {name}")
            commanders_done += 1

        elapsed = time.monotonic() - t0
        stopped = flag.is_set()
        if commanders_done > 0 and not stopped:
            _update_scrape_timing("stale", elapsed, commanders_done, limit_per_used=limit_per)

        deleted = _enforce_deck_limit(engine, log_fn)
        _JOBS[job_id].update(
            status="stopped" if stopped else "done",
            stats={
                "commanders_processed": commanders_done,
                "elapsed_s":            round(elapsed, 1),
                "deck_limit_deleted":   deleted,
                **total,
            },
        )
        threading.Thread(target=_refresh_stats_cache, daemon=True).start()
    except Exception as exc:
        _JOBS[job_id].update(status="error", error=str(exc))
    finally:
        _STOP_FLAGS.pop(job_id, None)


@router.post("/api/admin/scrape/stale")
def start_scrape_stale(
    count: int = Query(default=1, ge=1, le=100),
    limit_per: int = Query(default=200, ge=10, le=1000),
    headless: bool = Query(default=True),
    mode: str = Query(default="stale"),
    _user: dict = Depends(require_admin),
):
    if mode not in _STALE_QUERIES:
        mode = "stale"
    job_id = str(uuid.uuid4())
    _JOBS[job_id] = _new_job()
    _STOP_FLAGS[job_id] = threading.Event()
    t = threading.Thread(target=_run_scrape_stale, args=(job_id, count, limit_per, headless, mode), daemon=True)
    t.start()
    return _json_response({"job_id": job_id})


# ── Statut d'un job ──────────────────────────────────────────────────────────

@router.get("/api/admin/scrape/status/{job_id}")
def get_scrape_status(job_id: str, _user: dict = Depends(require_admin)):
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job inconnu")
    return _json_response(job)


@router.post("/api/admin/scrape/stop/{job_id}")
def stop_scrape(job_id: str, _user: dict = Depends(require_admin)):
    """Demande l'arrêt propre d'un job de scraping en cours.

    Pour le scrape récent : le batch courant se termine normalement, puis le
    job passe en statut « stopped ».
    Pour le scrape par commandant : le commandant en cours se termine, puis la
    boucle s'arrête avant le suivant.
    """
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job inconnu")
    if job["status"] != "running":
        raise HTTPException(status_code=400, detail=f"Job non actif (statut : {job['status']})")
    flag = _STOP_FLAGS.get(job_id)
    if flag:
        flag.set()
    job["progress"]["stopping"] = True
    return _json_response({"stopping": True})


# ── Stats dashboard ──────────────────────────────────────────────────────────

def _compute_stats(engine) -> dict:
    meta = _read_ml_metadata()
    s: dict = {
        "bronze_decks": 0, "bronze_rows": 0, "bronze_unique_commanders": 0,
        "bronze_commanders": 0, "commanders_with_decks": 0,
        "pending_commanders": 0, "scraped_recent": 0,
        "ml_commanders": 0, "ml_cards_global": 0, "ml_stat_entries": 0,
        "ml_last_trained_at": meta.get("last_trained_at"),
    }
    with engine.connect() as conn:
        s["bronze_decks"] = conn.execute(text("SELECT COUNT(DISTINCT deck_id) FROM deck_cards")).scalar() or 0
        s["bronze_rows"]  = conn.execute(text("SELECT COUNT(*) FROM deck_cards")).scalar() or 0
        s["bronze_unique_commanders"] = conn.execute(
            text("SELECT COUNT(DISTINCT commander) FROM deck_cards WHERE commander IS NOT NULL")
        ).scalar() or 0
        s["bronze_commanders"]  = conn.execute(text("SELECT COUNT(*) FROM commanders")).scalar() or 0
        s["commanders_with_decks"] = conn.execute(text(
            "SELECT COUNT(*) FROM commanders WHERE name IN "
            "(SELECT DISTINCT commander FROM deck_cards WHERE commander IS NOT NULL)"
        )).scalar() or 0
        s["pending_commanders"] = conn.execute(
            text("SELECT COUNT(*) FROM commanders WHERE last_scraped_at IS NULL")
        ).scalar() or 0
        s["scraped_recent"] = conn.execute(
            text("SELECT COUNT(*) FROM commanders WHERE last_scraped_at > NOW() - INTERVAL '7 days'")
        ).scalar() or 0
        try:
            s["ml_commanders"]   = conn.execute(text("SELECT COUNT(DISTINCT commander) FROM deck_stat_commander")).scalar() or 0
            s["ml_cards_global"] = conn.execute(text("SELECT COUNT(*) FROM deck_stat_global")).scalar() or 0
            s["ml_stat_entries"] = conn.execute(text("SELECT COUNT(*) FROM deck_stat_commander")).scalar() or 0
        except Exception:
            pass
    return s


def _refresh_stats_cache() -> dict:
    """Recalcule toutes les stats et met à jour _STATS_CACHE."""
    global _STATS_CACHE
    try:
        engine = _make_engine()
        _STATS_CACHE = _compute_stats(engine)
    except Exception:
        pass
    return _STATS_CACHE


@router.get("/api/admin/stats")
def get_admin_stats(_user: dict = Depends(require_admin)):
    if not _STATS_CACHE:
        _refresh_stats_cache()
    return _json_response(_STATS_CACHE)


@router.post("/api/admin/stats/refresh")
def refresh_admin_stats(_user: dict = Depends(require_admin)):
    """Force le recalcul complet depuis la DB et met à jour le cache."""
    s = _refresh_stats_cache()
    return _json_response(s)


# ── Liste des commandants ────────────────────────────────────────────────────

@router.get("/api/admin/commanders")
def list_commanders(_user: dict = Depends(require_admin)):
    try:
        engine = _make_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT c.name, c.rank, c.color_identity, c.decks_extracted,
                       c.last_scraped_at, COALESCE(d.cnt, 0) AS decks_in_db
                FROM commanders c
                LEFT JOIN (
                    SELECT commander, COUNT(DISTINCT deck_id) AS cnt
                    FROM deck_cards WHERE commander IS NOT NULL GROUP BY commander
                ) d ON d.commander = c.name
                ORDER BY last_scraped_at ASC NULLS FIRST, rank ASC NULLS LAST
            """)).fetchall()
        commanders_list = [
            {
                "name":            r.name,
                "rank":            r.rank,
                "color_identity":  r.color_identity or "",
                "decks_extracted": r.decks_extracted,
                "last_scraped_at": r.last_scraped_at.isoformat() if r.last_scraped_at else None,
                "decks_in_db":     r.decks_in_db,
            }
            for r in rows
        ]
    except Exception:
        commanders_list = []
    return _json_response({"commanders": commanders_list})


@router.post("/api/admin/commanders/sync")
def sync_commanders(_user: dict = Depends(require_admin)):
    try:
        engine = _make_engine()
        with engine.begin() as conn:
            result = conn.execute(text("""
                INSERT INTO commanders (name)
                SELECT DISTINCT commander FROM deck_cards
                WHERE commander IS NOT NULL
                ON CONFLICT (name) DO NOTHING
            """))
            added = result.rowcount
        return _json_response({"added": added})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── ML — réentraînement ───────────────────────────────────────────────────────

_ML_SCRIPT_STEPS = {
    "stats": ("Calcul des statistiques", "mox_to_stats.py"),
    "tfidf": ("Calcul TF-IDF",           "compute_commander_tfidf.py"),
}
_ML_ALL_STEPS = ["stats", "tfidf", "reload"]


def _run_ml_retrain(job_id: str, steps: list) -> None:
    import subprocess
    import sys
    import os as _os

    job = _JOBS[job_id]

    def log_fn(msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        j = _JOBS.get(job_id)
        if j is None:
            return
        j["log"].append({"ts": ts, "msg": msg})
        if len(j["log"]) > 1000:
            del j["log"][:200]

    scripts_dir = _os.path.normpath(
        _os.path.join(_os.path.dirname(__file__), "..", "..", "..", "scripts")
    )
    python = sys.executable
    step_total = len(steps)
    job["progress"]["decks_total"] = step_total

    for done, step_key in enumerate(steps, 1):
        if step_key in _ML_SCRIPT_STEPS:
            label, script_name = _ML_SCRIPT_STEPS[step_key]
            job["progress"]["phase"] = label
            job["progress"]["decks_done"] = done - 1
            log_fn(f"Étape {done}/{step_total} — {label}…")
            script_path = _os.path.join(scripts_dir, script_name)
            try:
                proc = subprocess.Popen(
                    [python, script_path],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        log_fn(line)
                proc.wait()
                if proc.returncode != 0:
                    _JOBS[job_id].update(
                        status="error",
                        error=f"{script_name} a échoué (code retour : {proc.returncode})",
                    )
                    return
            except Exception as exc:
                _JOBS[job_id].update(status="error", error=str(exc))
                return
            log_fn(f"✓ Étape {done} terminée.")

        elif step_key == "reload":
            job["progress"]["phase"] = "Rechargement du moteur IA"
            job["progress"]["decks_done"] = done - 1
            log_fn(f"Étape {done}/{step_total} — Rechargement du moteur IA…")
            try:
                from manamind.routers.engine import _get_engine
                import server
                server._deck_engine = None
                _get_engine()
                log_fn("✓ Moteur IA rechargé.")
            except Exception as exc:
                log_fn(f"  Avertissement rechargement : {exc}")

    trained_at = datetime.now(timezone.utc).isoformat()
    _write_ml_metadata({"last_trained_at": trained_at})
    _refresh_stats_cache()
    job["progress"]["decks_done"] = step_total
    _JOBS[job_id].update(status="done", stats={"steps_completed": step_total, "trained_at": trained_at})


# ── Timing scrape ────────────────────────────────────────────────────────────

@router.get("/api/admin/scrape/timing")
def get_scrape_timing(_user: dict = Depends(require_admin)):
    return _json_response(_read_scrape_metadata())


@router.post("/api/admin/scrape/timing/reset")
def reset_scrape_timing(
    mode: str = Query(..., description="'recent' ou 'stale'"),
    _user: dict = Depends(require_admin),
):
    if mode not in ("recent", "stale"):
        raise HTTPException(status_code=400, detail="mode doit être 'recent' ou 'stale'")
    meta = _read_scrape_metadata()
    meta.pop(mode, None)
    _write_scrape_metadata(meta)
    return _json_response({"reset": mode})


# ── ML — réentraînement ───────────────────────────────────────────────────────

@router.post("/api/admin/ml/retrain")
async def start_ml_retrain(request: Request, _user: dict = Depends(require_admin)):
    try:
        body = await request.json()
        steps = body.get("steps", _ML_ALL_STEPS)
    except Exception:
        steps = _ML_ALL_STEPS
    steps = [s for s in steps if s in {"stats", "tfidf", "reload"}]
    if not steps:
        steps = list(_ML_ALL_STEPS)
    job_id = str(uuid.uuid4())
    job = _new_job()
    job["progress"].update({"phase": "Initialisation", "decks_done": 0, "decks_total": len(steps)})
    _JOBS[job_id] = job
    t = threading.Thread(target=_run_ml_retrain, args=(job_id, steps), daemon=True)
    t.start()
    return _json_response({"job_id": job_id})
