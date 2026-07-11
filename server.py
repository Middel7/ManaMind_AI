#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import re
import unicodedata

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
import json as _json

def _json_response(data: dict, status_code: int = 200) -> Response:
    """JSONResponse avec support UTF-8 complet (pas d'échappement ASCII)."""
    return Response(
        content=_json.dumps(data, ensure_ascii=False),
        status_code=status_code,
        media_type="application/json; charset=utf-8",
    )
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
UPLOADS_DIR = ROOT / "uploads"
OUTPUTS_DIR = ROOT / "outputs"
OUTPUTS_RECO_DIR = ROOT / "outputs" / "recommendations"
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)
OUTPUTS_RECO_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT / "src"))
from manamind.recommandation_populaire import (  # noqa: E402
    DECKLISTS_ROOT as _POP_DECKLISTS_ROOT,
    load_deck_dataset,
    build_statistics,
    recommend_removals,
    normalize_name as _pop_normalize,
    BASIC_LANDS,
)


def _compute_removals(deck_content: str, commander_name: str, limit: int = 20) -> list[tuple[str, int, float]]:
    """
    Calcule les cartes à retirer via la logique recommandation_populaire.
    Utilisée par l'algorithme Analyse Populaire (V1).
    """
    import re as _re
    # Parser les cartes du deck depuis le contenu texte
    input_cards: set[str] = set()
    for line in deck_content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        m = _re.match(r"^\d+[xX]?\s+(.+)$", line)
        card_name = m.group(1).strip() if m else line
        input_cards.add(_pop_normalize(card_name))

    cmd_norm = _pop_normalize(commander_name)
    input_cards.discard(cmd_norm)
    input_cards -= BASIC_LANDS

    if not input_cards:
        return []

    try:
        decks = load_deck_dataset(_POP_DECKLISTS_ROOT)
        if not decks:
            return []
        deck_frequency, commander_decks, cooccurrence = build_statistics(decks)

        removals_norm = recommend_removals(
            input_cards=input_cards,
            deck_frequency=deck_frequency,
            commander=cmd_norm,
            commander_decks=commander_decks,
            cooccurrence=cooccurrence,
            commander_card=cmd_norm,
            limit=limit + 5,
        )

        # Nombre de decklists connues pour ce commandant (pour calculer le taux réel)
        nb_cmd_decks = len(commander_decks.get(cmd_norm, []))

        # Construire un reverse mapping normalisé → nom original depuis les decklists
        norm_to_original: dict[str, str] = {}
        import csv as _csv2
        cmd_dir = _POP_DECKLISTS_ROOT / _normalize_filename(commander_name)
        if not cmd_dir.exists():
            for sub in _POP_DECKLISTS_ROOT.iterdir():
                if sub.is_dir() and _normalize_filename(sub.name) == _normalize_filename(commander_name):
                    cmd_dir = sub
                    break
        if cmd_dir.exists():
            for csv_file in list(cmd_dir.glob("*.csv"))[:200]:
                try:
                    with open(csv_file, encoding="utf-8-sig", errors="replace") as f:
                        reader = _csv2.DictReader(f, delimiter=";")
                        for row in reader:
                            raw = (row.get("Card Name") or "").strip()
                            if raw:
                                norm_to_original[_pop_normalize(raw)] = raw
                except Exception:
                    continue

        cmd_lower = commander_name.lower()

        def restore(norm: str) -> str:
            return norm_to_original.get(norm, norm.title())

        result = [
            # support = nb decklists de CE commandant contenant la carte
            # taux = support / nb_cmd_decks → entre 0 et 1
            (restore(name), support, round(support / nb_cmd_decks, 4) if nb_cmd_decks > 0 else 0.0)
            for name, support, _raw_freq in removals_norm
            if restore(name).lower() != cmd_lower and name.lower() != cmd_lower
        ][:limit]

        return result
    except Exception as exc:
        print(f"[Retraits] Erreur : {exc}")
        return []


def _normalize_filename(name: str) -> str:
    """Convertit un nom de commandant en slug snake_case ASCII (cohérent avec le script V2)."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-zA-Z0-9\s]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name.lower()


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
    from sqlalchemy import func, select
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
    yield

app = FastAPI(lifespan=lifespan)

# ── Deck Improvement Engine — singleton préchargé au démarrage ───────────────
_deck_engine = None
_deck_engine_lock = None

def _get_deck_engine():
    """Retourne le DeckImprovementEngine (préchargé au démarrage, thread-safe)."""
    global _deck_engine, _deck_engine_lock
    import threading
    if _deck_engine_lock is None:
        _deck_engine_lock = threading.Lock()
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        ROOT / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )




@app.get("/results")
def results_page() -> FileResponse:
    return FileResponse(
        ROOT / "results.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.post("/upload-deck")
async def upload_deck(
    deckfile: UploadFile = File(...),
    algo: str = Form(default="v1"),
) -> JSONResponse:
    filename = Path(deckfile.filename).name
    deck_path = UPLOADS_DIR / filename
    deck_path.write_bytes(await deckfile.read())

    stem = Path(filename).stem

    output_path = OUTPUTS_DIR / f"recommendations_{stem}.csv"
    script = "src/manamind/recommandation_populaire.py"
    output_key = f"/outputs/recommendations_{stem}.csv"

    import os as _os
    _env = _os.environ.copy()
    _env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, script, "--input", str(deck_path), "--output", str(output_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        env=_env,
    )

    if result.returncode != 0:
        return JSONResponse({"error": result.stderr or "Erreur lors de la génération."}, status_code=500)

    return JSONResponse({
        "deckFile": f"/uploads/{filename}",
        "recommendationsFile": output_key,
    })




app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")
app.mount("/data",    StaticFiles(directory=str(ROOT / "data")), name="data")


# ── POST /api/deck/analyze ────────────────────────────────────────────────────
@app.post("/api/deck/analyze")
async def api_deck_analyze(request: Request) -> JSONResponse:
    """
    Analyse une decklist complète avec le Deck Improvement Engine.

    Body JSON :
        { "commander": "Teysa Karlov", "decklist": ["Blood Artist", ...] }

    Réponse :
        {
          "deck_score": 0.62,
          "coherence": 0.59,
          "distance_meta": 0.23,
          "profile": { "family_distribution": {...}, "mana_curve": {...}, ... },
          "gap": [ { "cluster_name": "...", "deck_share": 0.10, "meta_share": 0.19, "delta": -0.09 }, ... ],
          "top_additions": [ { "rank":1, "card_name":"...", "addition_score":0.61, ... }, ... ],
          "top_cuts":      [ { "rank":1, "card_name":"...", "cut_score":0.87,      ... }, ... ],
          "replacements":  [ { "rank":1, "cut_card":"...", "add_card":"...", "gain_delta":42.3, ... }, ... ],
        }
    """
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "JSON invalide"}, status_code=400)

    commander = (body.get("commander") or "").strip()
    decklist  = body.get("decklist") or []
    if not commander:
        return _json_response({"error": "commander manquant"}, status_code=400)
    if not isinstance(decklist, list) or len(decklist) == 0:
        return _json_response({"error": "decklist vide ou invalide"}, status_code=400)

    # Nettoyer : enlever le commandant s'il est dans la liste, terrains doublons tolérés
    decklist = [str(c).strip() for c in decklist if str(c).strip() and str(c).strip() != commander]

    try:
        engine = _get_deck_engine()
    except Exception as exc:
        return _json_response({"error": f"Moteur indisponible : {exc}"}, status_code=503)

    def _run_analysis():
        import time as _time
        t0 = _time.perf_counter()

        profile      = engine.analyze_deck(commander, decklist)
        gap_summaries, dist_meta = engine.gap_analysis(commander, profile, decklist)
        additions    = engine.generate_additions(commander, decklist, profile, gap_summaries)
        cuts         = engine.generate_cuts(commander, decklist, profile)
        replacements = engine.generate_replacements(cuts, additions)

        global_score = round(
            0.40 * profile.coherence_score
            + 0.35 * profile.avg_cosine_to_commander
            + 0.25 * max(0.0, 1.0 - dist_meta),
            4,
        )
        elapsed = round(_time.perf_counter() - t0, 2)

        cluster_profile = [
            {
                "cluster_id":   cid,
                "cluster_name": v["name"],
                "family":       v["family"],
                "deck_share":   round(v["share"] * 100, 1),
                "meta_share":   round(
                    engine.cmd_cluster_profile.get(commander, {}).get(cid, 0.0) * 100, 1
                ),
            }
            for cid, v in sorted(
                profile.cluster_distribution.items(),
                key=lambda x: -x[1]["share"]
            )[:10]
        ]

        gap_data = [
            {
                "cluster_id":   s.cluster_id,
                "cluster_name": s.name,
                "family":       s.family,
                "deck_share":   round(s.deck_share * 100, 1),
                "meta_share":   round(s.meta_share * 100, 1),
                "delta":        round(s.delta * 100, 1),
            }
            for s in gap_summaries
            if abs(s.delta) > 0.01
        ][:15]

        return {
            "commander":      commander,
            "deck_score":     global_score,
            "deck_score_100": round(global_score * 100),
            "coherence":      profile.coherence_score,
            "distance_meta":  dist_meta,
            "elapsed_s":      elapsed,
            "profile": {
                "card_count":          profile.card_count,
                "family_distribution": profile.family_distribution,
                "mana_curve":          profile.mana_curve,
                "color_distribution":  profile.color_distribution,
                "cluster_profile":     cluster_profile,
                "missing_in_corpus":   profile.missing_in_corpus[:10],
            },
            "gap": gap_data,
            "top_additions": [
                {
                    "rank":           i + 1,
                    "card_name":      a.card_name,
                    "addition_score": a.addition_score,
                    "score_100":      round(a.addition_score * 100),
                    "predicted_ir":   a.predicted_ir,
                    "cluster_name":   a.cluster_name,
                    "cluster_family": a.cluster_family,
                    "gap_bonus":      a.cluster_gap_bonus,
                    "deck_synergy":   a.deck_synergy,
                    "explanation":    a.explanation,
                    # Sous-scores normalisés /100 pour le tooltip d'explication
                    "sub_scores": {
                        "hybrid":   min(round(a.hybrid_score * 100), 100),
                        "edhrec":   min(round(a.predicted_ir), 100),
                        "gap":      min(round(a.cluster_gap_bonus * 100), 100),
                        "synergy":  min(round(a.deck_synergy * 100), 100),
                    },
                }
                for i, a in enumerate(additions)
            ],
            "top_cuts": [
                {
                    "rank":      i + 1,
                    "card_name": c.card_name,
                    "cut_score": c.cut_score,
                    "score_100": round(c.cut_score * 100),
                    "reasons":   c.reasons,
                }
                for i, c in enumerate(cuts)
            ],
            "replacements": [
                {
                    "rank":            r["rank"],
                    "cut_card":        r["cut_card"],
                    "add_card":        r["add_card"],
                    "gain_delta":      r["gain_delta"],
                    "cut_reasons":     r["cut_reasons"],
                    "add_explanation": r["add_explanation"],
                }
                for r in replacements
            ],
        }

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run_analysis)
        return _json_response(result)
    except Exception as exc:
        import traceback
        return _json_response(
            {"error": f"Erreur analyse : {exc}", "detail": traceback.format_exc()},
            status_code=500,
        )


# ── GET /api/deck/explanation ─────────────────────────────────────────────────
@app.get("/api/deck/explanation")
async def api_deck_explanation(
    commander: str = Query(..., description="Nom du commandant"),
    card:      str = Query(..., description="Nom de la carte"),
) -> JSONResponse:
    """
    Explication détaillée de la recommandation d'une carte pour un commandant.
    Calcule les 4 signaux hybrides et retourne une explication en langage naturel.
    """
    try:
        engine = _get_deck_engine()
    except Exception as exc:
        return _json_response({"error": f"Moteur indisponible : {exc}"}, status_code=503)

    try:
        commander = commander.strip()
        card      = card.strip()

        # Recalculer les signaux pour cette carte
        td        = engine.tfidf_lookup.get((commander, card), {})
        tfidf_n   = td.get("tfidf_norm", 0.0)
        idf       = td.get("idf", engine.card_idf.get(card, 0.0))
        real_ir   = td.get("inclusion_rate", 0.0)

        cosine   = engine._cosine_cmd(card, commander)
        cluster_s = engine._cluster_score(card, commander)
        tag_s    = engine._tag_score(card, commander)
        hybrid   = round(min(0.40*tfidf_n + 0.25*cosine + 0.20*cluster_s + 0.15*tag_s, 1.0), 4)
        ir_pred  = engine._predict_ir(card, commander, cosine, tfidf_n, idf)

        cid = engine.card_cluster.get(card)
        ann = engine.annotations.get(cid or -1, {})
        neighbors = engine.card_neighbors.get(card, [])[:3]

        reasons = []
        caveats = []

        if real_ir > 5:
            reasons.append(f"Jouée dans {real_ir:.0f}% des decks {commander}.")
        elif real_ir > 0:
            reasons.append(f"Présente dans {real_ir:.1f}% des decks {commander}.")
        else:
            caveats.append("Absente des decklists connues pour ce commandant.")

        if cosine > 0.5:
            reasons.append(f"Forte proximité vectorielle avec {commander} (cosine = {cosine:.3f}).")
        elif cosine > 0.2:
            reasons.append(f"Proximité modérée avec le style de {commander} (cosine = {cosine:.3f}).")

        if cid is not None and ann:
            family = engine.cluster_family.get(cid, "")
            meta_w = engine.cmd_cluster_profile.get(commander, {}).get(cid, 0.0)
            reasons.append(
                f"Appartient au cluster « {ann.get('name','')} » ({family}) "
                f"qui représente {meta_w*100:.0f}% du profil de {commander}."
            )

        if tag_s > 0.05:
            reasons.append(f"Tags Scryfall cohérents avec la stratégie (score tags : {tag_s:.3f}).")

        if neighbors:
            reasons.append(f"Proche vectoriellement de : {', '.join(neighbors)}.")

        reasons.append(f"Inclusion rate prédit : {ir_pred:.0f}%.")

        summary = (
            f"« {card} » obtient un score de recommandation de {hybrid:.3f}/1.000 "
            f"pour {commander}. Inclusion rate prédit : {ir_pred:.0f}%."
        )

        return _json_response({
            "commander":     commander,
            "card_name":     card,
            "hybrid_score":  hybrid,
            "predicted_ir":  round(ir_pred, 1),
            "real_ir":       round(real_ir, 1),
            "cluster":       ann.get("name", "—") if ann else "—",
            "cluster_family": engine.cluster_family.get(cid or -1, "—") if cid else "—",
            "summary":       summary,
            "reasons":       reasons,
            "caveats":       caveats,
        })
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=500)


@app.get("/api/cards/search")
def search_cards(
    q: str = Query(default="", description="Texte à rechercher dans le nom des cartes"),
    limit: int = Query(default=100, ge=1, le=100),
) -> JSONResponse:
    """
    Recherche de cartes par nom (contains, case-insensitive).
    Retourne au maximum 100 résultats triés par nom.
    Requiert que la base PostgreSQL soit configurée (.env) et que l'import ait été lancé.
    """
    if not _DB_AVAILABLE:
        return JSONResponse(
            {
                "error": (
                    "Base de données non configurée. "
                    "Crée un fichier .env avec DATABASE_URL puis lance "
                    "python scripts/import_scryfall_cards.py"
                )
            },
            status_code=503,
        )

    q = q.strip()
    if len(q) < 2:
        return JSONResponse({"cards": [], "total": 0, "query": q})

    try:
        with SessionLocal() as session:
            # Sous-requête 1a : rang de chaque impression par prix EUR décroissant
            # row_number() = 1 → impression la plus chère de la carte
            expensive_rank_subq = (
                select(
                    CardPrinting.card_id,
                    CardPrinting.id.label("pid"),
                    CardPrinting.image_normal,
                    CardPrinting.scryfall_uri,
                    func.row_number().over(
                        partition_by=CardPrinting.card_id,
                        order_by=CardPrice.price.desc().nulls_last(),
                    ).label("rn"),
                )
                .join(CardPrice, CardPrinting.id == CardPrice.printing_id)
                .where(
                    CardPrice.currency == "eur",
                    CardPrice.price_type == "regular",
                    CardPrice.price > 0,
                )
                .subquery()
            )
            expensive_printing_subq = (
                select(
                    expensive_rank_subq.c.card_id,
                    expensive_rank_subq.c.image_normal,
                    expensive_rank_subq.c.scryfall_uri,
                )
                .where(expensive_rank_subq.c.rn == 1)
                .subquery()
            )

            # Sous-requête 1b : première impression (fallback si aucun prix disponible)
            first_printing_subq = (
                select(
                    CardPrinting.card_id,
                    func.min(CardPrinting.id).label("pid"),
                )
                .group_by(CardPrinting.card_id)
                .subquery()
            )
            FallbackPrinting = aliased(CardPrinting)

            # Sous-requête 2 : prix EUR minimum (le moins cher) parmi toutes les impressions
            price_subq = (
                select(
                    CardPrinting.card_id,
                    func.min(CardPrice.price).label("eur_price"),
                )
                .join(CardPrice, CardPrinting.id == CardPrice.printing_id)
                .where(
                    CardPrice.currency == "eur",
                    CardPrice.price_type == "regular",
                    CardPrice.price > 0,
                )
                .group_by(CardPrinting.card_id)
                .subquery()
            )

            stmt = (
                select(
                    Card,
                    func.coalesce(
                        expensive_printing_subq.c.image_normal,
                        FallbackPrinting.image_normal,
                    ).label("image_normal"),
                    func.coalesce(
                        expensive_printing_subq.c.scryfall_uri,
                        FallbackPrinting.scryfall_uri,
                    ).label("scryfall_uri"),
                    price_subq.c.eur_price,
                )
                .outerjoin(expensive_printing_subq, Card.id == expensive_printing_subq.c.card_id)
                .outerjoin(first_printing_subq, Card.id == first_printing_subq.c.card_id)
                .outerjoin(FallbackPrinting, FallbackPrinting.id == first_printing_subq.c.pid)
                .outerjoin(price_subq, Card.id == price_subq.c.card_id)
                # ilike = ILIKE PostgreSQL : case-insensitive, paramétré → pas d'injection SQL
                .where(Card.name.ilike(f"%{q}%"))
                # Tri par popularité méta (1 = plus populaire), sans rank en dernier
                .order_by(Card.edhrec_rank.asc().nulls_last(), Card.name)
                .limit(limit)
            )

            rows = session.execute(stmt).all()

            cards = []
            for card, image_normal, scryfall_uri, eur_price in rows:
                oracle_text = card.oracle_text or ""
                if len(oracle_text) > 280:
                    oracle_text = oracle_text[:280] + "…"

                cards.append({
                    "name": card.name,
                    "mana_cost": card.mana_cost,
                    "type_line": card.type_line,
                    "oracle_text": oracle_text,
                    "legal_commander": card.legal_commander,
                    "colors": card.colors or [],
                    "edhrec_rank": card.edhrec_rank,
                    "eur_price": float(eur_price) if eur_price is not None else None,
                    "image_normal": image_normal,
                    "scryfall_uri": scryfall_uri,
                })

            return JSONResponse({"cards": cards, "total": len(cards), "query": q})

    except Exception as exc:
        return JSONResponse(
            {"error": f"Erreur base de données : {exc}"},
            status_code=500,
        )


@app.get("/api/cards/image")
async def card_image(
    name: str = Query(..., description="Nom exact ou approché de la carte"),
) -> JSONResponse:
    """
    Retourne l'URL de l'image normale d'une carte.
    Cherche d'abord dans la DB locale, puis appelle Scryfall côté serveur (pas de CORS).
    """
    import httpx

    name = name.strip()

    # Scryfall (côté serveur → pas de CORS) — source unique pour image + colors + booster
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.get(
                "https://api.scryfall.com/cards/named",
                params={"fuzzy": name},
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                url = (
                    data.get("image_uris", {}).get("normal")
                    or (data.get("card_faces") or [{}])[0].get("image_uris", {}).get("normal")
                )
                colors = data.get("color_identity") or data.get("colors") or []
                rarity = data.get("rarity", "")
                set_code = data.get("set", "").upper()
                if url:
                    return _json_response({"url": url, "colors": colors, "rarity": rarity, "set": set_code})
    except Exception:
        pass

    # Fallback DB locale (image uniquement, pas de metadata couleur)
    if _DB_AVAILABLE:
        try:
            with SessionLocal() as session:
                stmt = (
                    select(CardPrinting.image_normal)
                    .join(Card, Card.id == CardPrinting.card_id)
                    .where(
                        Card.name.ilike(name),
                        CardPrinting.image_normal.isnot(None),
                        CardPrinting.lang == "en",
                    )
                    .limit(1)
                )
                row = session.execute(stmt).first()
                if row and row[0]:
                    return _json_response({"url": row[0], "colors": [], "booster": None})
        except Exception:
            pass

    return _json_response({"url": None}, status_code=404)


@app.get("/api/card-source")
def api_card_source(request: Request, name: str = Query(...)) -> JSONResponse:
    """
    Retourne la source d'une carte parmi : 'in_deck', 'collection', 'opened_sets', ou None.
    Même logique de priorité que deck_build.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    import unicodedata as _ud
    def _norm(s: str) -> str:
        s = _ud.normalize("NFKD", s)
        return "".join(c for c in s if not _ud.combining(c)).lower().strip()

    card_norm = _norm(name)

    # 1. Dans un deck de l'utilisateur ?
    from manamind.user_decks import load_config_for_user, get_deck_cards
    decks_found: list[str] = []
    for entry in load_config_for_user(user["id"]):
        commander = entry.get("commander", "")
        if not commander:
            continue
        try:
            entries = get_deck_cards(user["id"], commander)
            if entries and any(_norm(cn) == card_norm for cn, _ in entries):
                decks_found.append(commander)
        except Exception:
            continue
    if decks_found:
        return _json_response({"source": "in_deck", "decks": decks_found})

    # 2. Dans la collection de l'utilisateur ?
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    with SessionLocal() as sess:
        row = sess.execute(_t("""
            SELECT 1 FROM user_collection
            WHERE user_id = :uid AND LOWER(TRIM(card_name)) = LOWER(TRIM(:name))
            LIMIT 1
        """), {"uid": user["id"], "name": name}).fetchone()
    if row:
        return _json_response({"source": "collection", "decks": []})

    # 3. C/U dans un set ouvert (Opened.txt) ?
    opened_file = ROOT / "Opened.txt"
    if opened_file.exists():
        from manamind.collection_advisor import load_opened_set_cards
        opened_cards = load_opened_set_cards()
        if card_norm in opened_cards:
            return _json_response({"source": "opened_sets", "decks": []})

    return _json_response({"source": None, "decks": []})


@app.get("/api/card-in-decks")
def api_card_in_decks(request: Request, name: str = Query(...)) -> JSONResponse:
    """
    Retourne la liste des commandants dont le deck contient la carte `name`.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    import unicodedata as _ud
    def _norm(s: str) -> str:
        s = _ud.normalize("NFKD", s)
        return "".join(c for c in s if not _ud.combining(c)).lower().strip()

    from manamind.user_decks import load_config_for_user, get_deck_cards
    card_norm = _norm(name)
    decks_found: list[str] = []
    for entry in load_config_for_user(user["id"]):
        commander = entry.get("commander", "")
        if not commander:
            continue
        try:
            entries = get_deck_cards(user["id"], commander)
            if entries and any(_norm(cn) == card_norm for cn, _ in entries):
                decks_found.append(commander)
        except Exception:
            continue
    return _json_response({"decks": decks_found})


@app.get("/api/opened-sets")
def api_opened_sets() -> JSONResponse:
    """Retourne la liste des codes de sets ouverts (Opened.txt)."""
    opened_file = ROOT / "Opened.txt"
    if not opened_file.exists():
        return _json_response({"sets": []})
    codes = [l.strip().upper() for l in opened_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    return _json_response({"sets": codes})


@app.get("/api/cards/price")
def get_card_price(
    name: str = Query(..., description="Nom exact de la carte (anglais)"),
) -> JSONResponse:
    """
    Retourne le prix EUR minimum (regular) de la carte parmi toutes ses impressions.
    Cherche d'abord en DB, puis fallback Scryfall.
    """
    if _DB_AVAILABLE:
        try:
            with SessionLocal() as session:
                stmt = (
                    select(func.min(CardPrice.price))
                    .join(CardPrinting, CardPrinting.id == CardPrice.printing_id)
                    .join(Card, Card.id == CardPrinting.card_id)
                    .where(
                        Card.name.ilike(name),
                        CardPrice.currency == "eur",
                        CardPrice.price_type == "regular",
                        CardPrice.price > 0,
                    )
                )
                row = session.execute(stmt).first()
                if row and row[0] is not None:
                    return _json_response({"price": float(row[0]), "currency": "EUR"})
        except Exception:
            pass

    return _json_response({"price": None, "currency": "EUR"})


@app.get("/api/cards/autocomplete")
def autocomplete_cards(
    q: str = Query(default="", description="Préfixe à rechercher"),
    limit: int = Query(default=8, ge=1, le=20),
) -> JSONResponse:
    """
    Autocomplete sur les noms de cartes (starts-with, case-insensitive).
    Cherche dans Card.name (anglais) ET CardPrinting.printed_name (toutes langues).
    Retourne les noms anglais canoniques dédupliqués.
    """
    if not _DB_AVAILABLE:
        return _json_response({"names": []})

    q = q.strip()
    if len(q) < 2:
        return _json_response({"names": []})

    try:
        with SessionLocal() as session:
            # Noms anglais commençant par q
            en_stmt = (
                select(Card.name)
                .where(Card.name.ilike(f"{q}%"))
                .order_by(Card.edhrec_rank.asc().nulls_last(), Card.name)
                .limit(limit)
            )
            en_names = [row[0] for row in session.execute(en_stmt).all()]

            # Noms traduits commençant par q → récupérer le nom anglais canonique
            tr_stmt = (
                select(Card.name)
                .join(CardPrinting, Card.id == CardPrinting.card_id)
                .where(
                    CardPrinting.printed_name.isnot(None),
                    CardPrinting.printed_name.ilike(f"{q}%"),
                )
                .order_by(Card.edhrec_rank.asc().nulls_last(), Card.name)
                .limit(limit)
            )
            tr_names = [row[0] for row in session.execute(tr_stmt).all()]

            # Fusionner en conservant l'ordre et dédupliquer
            seen: set[str] = set()
            names: list[str] = []
            for name in en_names + tr_names:
                if name not in seen:
                    seen.add(name)
                    names.append(name)
                if len(names) >= limit:
                    break

            return _json_response({"names": names})

    except Exception as exc:
        return _json_response({"names": [], "error": str(exc)})


@app.get("/api/collection-suggest")
def api_collection_suggest(
    request: Request,
    top: int = Query(default=40, ge=1, le=100),
    commander: str | None = Query(default=None),
) -> JSONResponse:
    """
    Analyse la collection et les decks de l'utilisateur,
    et retourne les cartes disponibles ayant le meilleur taux d'inclusion.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.collection_advisor import suggest_from_collection_for_user
    result = suggest_from_collection_for_user(user["id"], top_n=top, commander_filter=commander or None)
    return _json_response(result)


@app.get("/api/deck-composition")
def api_deck_composition(
    request: Request,
    commander: str = Query(...),
) -> JSONResponse:
    """
    Retourne la composition par type du deck personnel et la moyenne méta
    pour le commandant donné.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.collection_advisor import compute_deck_composition_for_user
    result = compute_deck_composition_for_user(user["id"], commander)
    return _json_response(result)


@app.get("/deck-moves")
def deck_moves_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_moves.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/deck-moves")
def api_deck_moves(
    request: Request,
    top: int = Query(default=60, ge=1, le=100),
) -> JSONResponse:
    """
    Retourne les cartes présentes dans un deck mais qui auraient un meilleur taux
    d'inclusion dans un autre deck du même joueur, classées par gain décroissant.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.collection_advisor import suggest_moves_for_user
    result = suggest_moves_for_user(user["id"], top_n=top)
    return _json_response(result)


@app.get("/deck-trim")
def deck_trim_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_trim.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/deck-trim")
def api_deck_trim(
    request: Request,
    commander: str = Query(..., description="Nom exact du commandant"),
) -> JSONResponse:
    """
    Retourne les cartes du deck du commandant triées par inclusion_rate croissant.
    Les candidates à la coupe sont en tête de liste.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.collection_advisor import suggest_cuts_for_user
    result = suggest_cuts_for_user(user["id"], commander_name=commander)
    return _json_response(result)


@app.get("/api/deck-baseland-counts")
def api_deck_baseland_counts(
    request: Request,
    commander: str = Query(...),
) -> JSONResponse:
    """Retourne { counts: { 'Plains': 5, 'Island': 3, ... } } pour les terrains de base du deck."""
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.user_decks import get_deck_cards
    from manamind.collection_advisor import _normalize
    BASIC_LANDS = {"plains": "Plains", "island": "Island", "swamp": "Swamp",
                   "mountain": "Mountain", "forest": "Forest", "wastes": "Wastes"}
    entries = get_deck_cards(user["id"], commander)
    counts: dict[str, int] = {}
    for name, qty in entries:
        norm = _normalize(name)
        if norm in BASIC_LANDS:
            canonical = BASIC_LANDS[norm]
            counts[canonical] = counts.get(canonical, 0) + qty
    return _json_response({"counts": counts})


@app.get("/api/my-decks")
def api_my_decks(request: Request) -> JSONResponse:
    """
    Retourne la liste des decks de l'utilisateur connecté.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.user_decks import load_config_for_user

    raw = load_config_for_user(user["id"])
    decks = []
    for entry in raw:
        cmd = entry.get("commander", "")
        if not cmd:
            continue
        d = {
            "commander": cmd,
            "source": "moxfield",
            "deck_id": entry["deck_id"],
            "url": entry.get("url", ""),
        }
        if entry.get("fetched_at") and hasattr(entry["fetched_at"], "isoformat"):
            d["fetched_at"] = entry["fetched_at"].isoformat()
        decks.append(d)

    decks.sort(key=lambda d: d["commander"].lower())
    return _json_response({"decks": decks})


# ── Moxfield config ───────────────────────────────────────────────────────────

@app.get("/deck-config")
def deck_config_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_config.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/moxfield-decks")
def api_moxfield_list(request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.user_decks import load_config_for_user
    decks = load_config_for_user(user["id"])
    # Sérialiser les datetimes
    for d in decks:
        if d.get("fetched_at") and hasattr(d["fetched_at"], "isoformat"):
            d["fetched_at"] = d["fetched_at"].isoformat()
    return _json_response({"decks": decks})


@app.post("/api/moxfield-decks")
async def api_moxfield_add(request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    body = await request.json()
    url = (body.get("url") or "").strip()
    if not url:
        return _json_response({"error": "URL manquante"}, status_code=400)
    try:
        from manamind.moxfield_client import add_or_update_deck, _deck_id_from_url, _fetch_from_api, _extract_commander, _parse_cards
        from manamind.user_decks import save_deck_for_user, set_deck_cards
        deck_id = _deck_id_from_url(url)
        if not deck_id:
            return _json_response({"error": "URL Moxfield invalide"}, status_code=400)
        data = _fetch_from_api(deck_id)
        commander = _extract_commander(data)
        name = data.get("name", "")
        save_deck_for_user(user["id"], deck_id, url, commander, name)
        cards = _parse_cards(data)
        set_deck_cards(user["id"], commander, cards)
        return _json_response({"ok": True, "deck": {"deck_id": deck_id, "url": url, "commander": commander, "name": name}})
    except ValueError as e:
        return _json_response({"error": str(e)}, status_code=400)


@app.post("/api/moxfield-decks/{deck_id}/refresh")
def api_moxfield_refresh(deck_id: str, request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    try:
        from manamind.user_decks import load_config_for_user, save_deck_for_user, set_deck_cards, mark_locally_modified
        from manamind.moxfield_client import _fetch_from_api, _extract_commander, _parse_cards
        decks = load_config_for_user(user["id"])
        entry = next((d for d in decks if d["deck_id"] == deck_id), None)
        if not entry:
            return _json_response({"error": "Deck introuvable"}, status_code=404)
        data = _fetch_from_api(deck_id)
        commander = _extract_commander(data)
        name = data.get("name", entry.get("name", ""))
        save_deck_for_user(user["id"], deck_id, entry.get("url", ""), commander, name)
        set_deck_cards(user["id"], commander, _parse_cards(data))
        mark_locally_modified(user["id"], deck_id, False)
        return _json_response({"ok": True, "deck": {"deck_id": deck_id, "commander": commander, "name": name}})
    except ValueError as e:
        return _json_response({"error": str(e)}, status_code=400)


@app.delete("/api/moxfield-decks/{deck_id}")
def api_moxfield_delete(deck_id: str, request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.user_decks import remove_deck_for_user
    ok = remove_deck_for_user(user["id"], deck_id)
    return _json_response({"ok": ok})


@app.post("/api/deck-card/add")
async def api_deck_card_add(request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    body = await request.json()
    commander = (body.get("commander") or "").strip()
    card      = (body.get("card_name") or "").strip()
    if not commander or not card:
        return _json_response({"error": "Paramètres manquants"}, status_code=400)
    try:
        from manamind.user_decks import add_card_to_deck_db
        add_card_to_deck_db(user["id"], commander, card)
        return _json_response({"ok": True})
    except Exception as e:
        return _json_response({"error": str(e)}, status_code=500)


@app.post("/api/deck-card/remove")
async def api_deck_card_remove(request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    body = await request.json()
    commander = (body.get("commander") or "").strip()
    card      = (body.get("card_name") or "").strip()
    if not commander or not card:
        return _json_response({"error": "Paramètres manquants"}, status_code=400)
    try:
        from manamind.user_decks import remove_card_from_deck_db
        found = remove_card_from_deck_db(user["id"], commander, card)
        if not found:
            return _json_response({"ok": False, "error": f"« {card} » n'est pas dans le deck de {commander}"}, status_code=404)
        return _json_response({"ok": True})
    except Exception as e:
        return _json_response({"error": str(e)}, status_code=500)


@app.get("/api/deck-txt/{deck_id}")
def api_deck_txt(deck_id: str, request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.user_decks import load_config_for_user, get_deck_txt_content
    decks = load_config_for_user(user["id"])
    entry = next((d for d in decks if d["deck_id"] == deck_id), None)
    if not entry:
        return _json_response({"error": "Deck introuvable"}, status_code=404)
    content = get_deck_txt_content(user["id"], entry["commander"])
    return _json_response({"ok": True, "content": content or "", "commander": entry["commander"]})


@app.post("/api/deck-txt/{deck_id}/mark-synced")
def api_deck_mark_synced(deck_id: str) -> JSONResponse:
    from manamind.moxfield_client import load_config, mark_as_synced
    decks = load_config()
    entry = next((d for d in decks if d["deck_id"] == deck_id), None)
    if not entry:
        return _json_response({"error": "Deck introuvable"}, status_code=404)
    ok = mark_as_synced(deck_id, entry["commander"])
    return _json_response({"ok": ok})


@app.get("/deck-build")
def deck_build_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_build.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/commander-suggest")
def commander_suggest_page() -> FileResponse:
    return FileResponse(
        ROOT / "commander_suggest.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/card-inclusion")
def api_card_inclusion(
    card: str = Query(...),
    commander: str = Query(...),
) -> JSONResponse:
    """Taux d'inclusion d'une carte pour un commandant donné."""
    from manamind.card_commander_matcher import _normalize, _load_frequency_index
    idx = _load_frequency_index()
    cmd_norm  = _normalize(commander)
    card_norm = _normalize(card)
    cmd_data  = idx.get(cmd_norm, {})
    entry     = cmd_data.get(card_norm)
    if entry is None:
        return _json_response({"inclusion_rate": None})
    return _json_response({
        "inclusion_rate": round(entry["inclusion_rate"], 1),
        "decks_with_card": entry["decks_with_card"],
        "total_decks": entry["total_decks"],
    })


@app.get("/api/commander-suggest")
def api_commander_suggest(
    card: str = Query(..., description="Nom de la carte à rechercher"),
    top: int = Query(default=3, ge=1, le=10),
    mode: str = Query(default="mine", description="'mine' = mes commandants, 'all' = tous"),
) -> JSONResponse:
    """
    mode='mine' : parmi les commandants de data/My_commanders.txt (comportement original)
    mode='all'  : tous les commandants de la DB, avec flag in_my_decks
    """
    from manamind.card_commander_matcher import suggest_commanders, load_allowed_commanders
    from sqlalchemy import text as _text

    card = card.strip()
    if not card:
        return _json_response({"error": "Paramètre 'card' manquant"}, status_code=400)

    if mode == "mine":
        results = suggest_commanders(card, top_n=top)
        return _json_response({"card": card, "mode": "mine", "suggestions": results})

    # mode == "all" : requête globale sans filtre commandant
    import unicodedata as _ud

    def _norm(s: str) -> str:
        s = _ud.normalize("NFKD", s)
        return "".join(c for c in s if not _ud.combining(c)).lower().strip()

    my_commanders = {_norm(c) for c in load_allowed_commanders()}

    with SessionLocal() as session:
        rows = session.execute(_text("""
            SELECT dsc.commander, dsc.inclusion_rate, dsc.decks_with_card, dsc.total_decks,
                   (
                     SELECT MIN(p2.image_normal)
                     FROM scryfall_cards sc2
                     JOIN scryfall_card_printings p2
                       ON p2.card_id = sc2.id AND p2.lang = 'en' AND p2.image_normal IS NOT NULL
                     WHERE LOWER(TRIM(sc2.name)) = LOWER(TRIM(
                       SPLIT_PART(dsc.commander, ' & ', 1)
                     ))
                   ) AS image_url
            FROM deck_stat_commander dsc
            WHERE dsc.card_name = :card
            GROUP BY dsc.commander, dsc.inclusion_rate, dsc.decks_with_card, dsc.total_decks
            ORDER BY dsc.inclusion_rate DESC
            LIMIT :top
        """), {"card": card, "top": top}).fetchall()

    suggestions = []
    for row in rows:
        suggestions.append({
            "commander":       row.commander,
            "inclusion_rate":  round(row.inclusion_rate, 2),
            "decks_with_card": row.decks_with_card,
            "total_decks":     row.total_decks,
            "image_url":       row.image_url,
            "in_my_decks":     _norm(row.commander) in my_commanders,
        })

    return _json_response({"card": card, "mode": "all", "suggestions": suggestions})


@app.get("/api/engine/status")
def api_engine_status() -> JSONResponse:
    """Retourne l'état de chargement du DeckImprovementEngine."""
    ready = _deck_engine is not None
    return _json_response({"ready": ready})


@app.post("/api/engine/start")
async def api_engine_start(request: Request) -> JSONResponse:
    """Lance le chargement du moteur IA manuellement (admin uniquement)."""
    from manamind.auth import get_current_user, COOKIE_NAME
    from fastapi import HTTPException
    import asyncio, threading
    try:
        user = await asyncio.get_event_loop().run_in_executor(
            None, lambda: get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
        )
        if user.get("role") != "admin":
            return _json_response({"error": "Accès réservé à l'admin"}, status_code=403)
    except HTTPException as e:
        return _json_response({"error": e.detail}, status_code=e.status_code)

    if _deck_engine is not None:
        return _json_response({"status": "already_ready"})

    def _load():
        try:
            _get_deck_engine()
        except Exception as exc:
            import logging
            logging.getLogger("manamind").warning(f"Chargement engine échoué : {exc}")

    threading.Thread(target=_load, daemon=True, name="engine-manual-start").start()
    return _json_response({"status": "starting"})


@app.post("/api/deck-freq")
async def api_deck_freq(request: Request) -> JSONResponse:
    """
    Retourne le taux d'inclusion (%) par carte pour un commandant donné.
    Body: { "commander": "Kyler, Sigardian Emissary", "cards": ["Swords to Plowshares", ...] }
    Réponse: { "rates": { "Swords to Plowshares": 34.2, ... } }
    """
    body = await request.json()
    commander = (body.get("commander") or "").strip()
    cards: list[str] = body.get("cards") or []
    if not commander or not cards:
        return _json_response({"rates": {}})

    from sqlalchemy import text as _t
    with SessionLocal() as sess:
        rows = sess.execute(_t("""
            SELECT card_name,
                   ROUND(inclusion_rate::numeric, 1) AS pct
            FROM deck_stat_commander
            WHERE LOWER(TRIM(commander)) = LOWER(TRIM(:cmd))
              AND card_name = ANY(:cards)
        """), {"cmd": commander, "cards": cards}).fetchall()

    rates = {r.card_name: float(r.pct) for r in rows}
    return _json_response({"rates": rates})


@app.get("/collection-commanders")
def page_collection_commanders() -> FileResponse:
    return FileResponse(ROOT / "collection_commanders.html")


def _load_deck_usage_for_user(user_id: int) -> dict[str, int]:
    """Retourne {card_name_lower: nb_decks_où_utilisée} depuis la DB."""
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal as _SL
    with _SL() as sess:
        rows = sess.execute(_t("""
            SELECT LOWER(TRIM(card_name)) AS cn, COUNT(DISTINCT commander) AS cnt
            FROM user_deck_cards
            WHERE user_id = :uid
            GROUP BY LOWER(TRIM(card_name))
        """), {"uid": user_id}).fetchall()
    return {r.cn: r.cnt for r in rows}


@app.get("/api/collection-commanders")
def api_collection_commanders(
    request: Request,
    top: int = Query(default=10, ge=1, le=50),
    mode: str = Query(default="available"),  # "available" | "all"
) -> Response:
    """
    Retourne les `top` commandants pour lesquels la collection couvre
    la plus grande valeur (Cardmarket low_price) parmi leurs 100 cartes
    les plus jouées. Images Scryfall incluses.
    mode="available" : seulement les cartes non utilisées dans les decks
    mode="all"       : toute la collection sans filtre deck
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from sqlalchemy import text
    import json as _j

    deck_usage = _load_deck_usage_for_user(user["id"]) if mode == "available" else {}

    def _sql_str(v: str) -> str:
        return "'" + v.replace("'", "''") + "'"

    with SessionLocal() as s:
        collection_rows = s.execute(text(
            "SELECT card_name, quantity FROM user_collection WHERE user_id = :uid"
        ), {"uid": user["id"]}).fetchall()

        # Construire la collection selon le mode
        coll: dict[str, int] = {}
        for r in collection_rows:
            name_lower = r.card_name.strip().lower()
            if mode == "available":
                dispo = r.quantity - deck_usage.get(name_lower, 0)
                if dispo > 0:
                    coll[name_lower] = dispo
            else:
                coll[name_lower] = r.quantity

        if not coll:
            return _json_response({"commanders": []})

        values_rows = ", ".join(f"({_sql_str(k)}, {v})" for k, v in coll.items())
        avail_cte = f"avail(card_name_lower, qty) AS (VALUES {values_rows})"

        rows = s.execute(text(f"""
            WITH {avail_cte},
            top100 AS (
                SELECT commander, card_name,
                       ROW_NUMBER() OVER (PARTITION BY commander ORDER BY inclusion_rate DESC) AS rk
                FROM deck_stat_commander
            ),
            top100_filtered AS (
                SELECT commander, card_name FROM top100 WHERE rk <= 100
            ),
            collection_match AS (
                SELECT t.commander, t.card_name, a.qty AS qty
                FROM top100_filtered t
                JOIN avail a ON a.card_name_lower = LOWER(TRIM(t.card_name))
            ),
            prices AS (
                SELECT cp.en_name AS card_name,
                       MIN(pe.low_price) AS low_price
                FROM cardmarket_products cp
                JOIN cardmarket_price_guide_entries pe ON pe.id_product = cp.id_product
                WHERE pe.low_price IS NOT NULL AND pe.low_price > 0
                GROUP BY cp.en_name
            ),
            card_images AS (
                SELECT LOWER(TRIM(sc.name)) AS name_lower,
                       MIN(p.image_normal) AS image_url
                FROM scryfall_cards sc
                JOIN scryfall_card_printings p ON p.card_id = sc.id
                WHERE p.image_normal IS NOT NULL AND p.lang = 'en'
                GROUP BY LOWER(TRIM(sc.name))
            ),
            scored AS (
                SELECT cm.commander, cm.card_name, cm.qty,
                       COALESCE(pr.low_price, 0) AS low_price,
                       ci.image_url AS card_image
                FROM collection_match cm
                LEFT JOIN prices pr ON LOWER(TRIM(pr.card_name)) = LOWER(TRIM(cm.card_name))
                LEFT JOIN card_images ci ON ci.name_lower = LOWER(TRIM(cm.card_name))
            ),
            cmd_images AS (
                SELECT LOWER(TRIM(sc.name)) AS name_lower,
                       MIN(p.image_normal) AS image_url
                FROM scryfall_cards sc
                JOIN scryfall_card_printings p ON p.card_id = sc.id
                WHERE p.image_normal IS NOT NULL AND p.lang = 'en'
                GROUP BY LOWER(TRIM(sc.name))
            )
            SELECT s.commander,
                   SUM(s.low_price) AS total_value,
                   COUNT(*) AS card_count,
                   ci2.image_url AS commander_image,
                   JSON_AGG(
                       JSON_BUILD_OBJECT(
                           'card_name', s.card_name,
                           'low_price', ROUND(s.low_price::numeric, 2),
                           'qty', s.qty,
                           'image_url', s.card_image
                       ) ORDER BY s.low_price DESC
                   ) AS cards
            FROM scored s
            LEFT JOIN cmd_images ci2 ON ci2.name_lower = LOWER(TRIM(s.commander))
            GROUP BY s.commander, ci2.image_url
            ORDER BY total_value DESC
            LIMIT :top
        """), {"top": top}).fetchall()

    result = []
    for r in rows:
        cards = r.cards if isinstance(r.cards, list) else _j.loads(r.cards)
        result.append({
            "commander": r.commander,
            "commander_image": r.commander_image,
            "total_value": round(float(r.total_value or 0), 2),
            "card_count": r.card_count,
            "cards": cards,
        })
    return _json_response({"commanders": result})


# ═══════════════════════════════════════════════════════════════════
#  AUTHENTIFICATION
# ═══════════════════════════════════════════════════════════════════

@app.get("/login")
def login_page() -> FileResponse:
    return FileResponse(ROOT / "login.html", media_type="text/html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/register")
def register_page() -> FileResponse:
    return FileResponse(ROOT / "register.html", media_type="text/html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.post("/auth/login")
async def auth_login(request: Request) -> Response:
    from manamind.auth import get_user_by_email, verify_password, create_token, COOKIE_NAME, EXPIRE_DAYS
    body = await request.json()
    email    = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return _json_response({"error": "Email et mot de passe requis"}, status_code=400)

    user = get_user_by_email(email)
    if user is None or not user.get("password_hash"):
        return _json_response({"error": "Identifiants invalides"}, status_code=401)
    if not verify_password(password, user["password_hash"]):
        return _json_response({"error": "Identifiants invalides"}, status_code=401)
    if not user["is_active"]:
        return _json_response({"error": "Compte désactivé"}, status_code=403)

    token = create_token(user["id"])

    # Mettre à jour last_login_at
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    with SessionLocal() as sess:
        sess.execute(_t("UPDATE users SET last_login_at = NOW() WHERE id = :id"), {"id": user["id"]})
        sess.commit()

    resp = _json_response({"ok": True, "display_name": user["display_name"], "role": user["role"]})
    resp.set_cookie(
        key=COOKIE_NAME, value=token,
        httponly=True, samesite="lax", secure=False,
        max_age=EXPIRE_DAYS * 86400,
    )
    return resp


@app.post("/auth/logout")
def auth_logout() -> Response:
    from manamind.auth import COOKIE_NAME
    resp = _json_response({"ok": True})
    resp.delete_cookie(key=COOKIE_NAME)
    return resp


@app.post("/auth/register")
async def auth_register(request: Request) -> Response:
    from manamind.auth import (validate_invitation, consume_invitation,
                                create_user, create_token, COOKIE_NAME, EXPIRE_DAYS)
    body = await request.json()
    token        = (body.get("token") or "").strip()
    email        = (body.get("email") or "").strip().lower()
    password     = body.get("password") or ""
    display_name = (body.get("display_name") or "").strip()

    if not token:
        return _json_response({"error": "Lien d'invitation manquant"}, status_code=400)
    if not email or "@" not in email:
        return _json_response({"error": "Email invalide"}, status_code=400)
    if len(password) < 8:
        return _json_response({"error": "Mot de passe trop court (8 caractères min)"}, status_code=400)

    # Valider l'invitation
    from fastapi import HTTPException
    try:
        inv_id = validate_invitation(token)
    except HTTPException as e:
        return _json_response({"error": e.detail}, status_code=400)

    # Vérifier email unique
    from manamind.auth import get_user_by_email
    if get_user_by_email(email):
        return _json_response({"error": "Cet email est déjà utilisé"}, status_code=400)

    # Créer le compte
    user_id = create_user(email, password, role="user", display_name=display_name or None)
    consume_invitation(inv_id, user_id)

    jwt_token = create_token(user_id)
    resp = _json_response({"ok": True, "display_name": display_name or email.split("@")[0]})
    resp.set_cookie(
        key=COOKIE_NAME, value=jwt_token,
        httponly=True, samesite="lax", secure=False,
        max_age=EXPIRE_DAYS * 86400,
    )
    return resp


@app.get("/auth/me")
async def auth_me(request: Request) -> Response:
    """Retourne les infos de l'utilisateur connecté (pour les pages HTML)."""
    from manamind.auth import get_current_user, COOKIE_NAME
    from fastapi import HTTPException
    import asyncio
    token = request.cookies.get(COOKIE_NAME)
    try:
        loop = asyncio.get_event_loop()
        user = await loop.run_in_executor(None, lambda: get_current_user(mm_token=token))
        return _json_response({"authenticated": True, "user": user})
    except HTTPException:
        return _json_response({"authenticated": False})


# ═══════════════════════════════════════════════════════════════════
#  ADMIN
# ═══════════════════════════════════════════════════════════════════

@app.get("/admin")
def admin_page() -> FileResponse:
    return FileResponse(ROOT / "admin.html", media_type="text/html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/api/admin/users")
def api_admin_users(request: Request) -> Response:
    from manamind.auth import require_admin, COOKIE_NAME
    require_admin(mm_token=request.cookies.get(COOKIE_NAME))
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    with SessionLocal() as sess:
        rows = sess.execute(_t("""
            SELECT u.id, u.email, u.display_name, u.role, u.is_active, u.created_at, u.last_login_at,
                   (SELECT COUNT(*) FROM user_collection uc WHERE uc.user_id = u.id) AS collection_count,
                   (SELECT COUNT(*) FROM user_moxfield_decks umd WHERE umd.user_id = u.id) AS deck_count
            FROM users u ORDER BY u.created_at DESC
        """)).fetchall()
    users = [dict(r._mapping) for r in rows]
    for u in users:
        if u.get("created_at"): u["created_at"] = u["created_at"].isoformat()
        if u.get("last_login_at"): u["last_login_at"] = u["last_login_at"].isoformat()
    return _json_response({"users": users})


@app.post("/api/admin/users/{user_id}/toggle")
def api_admin_toggle_user(user_id: int, request: Request) -> Response:
    from manamind.auth import require_admin, COOKIE_NAME
    admin = require_admin(mm_token=request.cookies.get(COOKIE_NAME))
    if admin["id"] == user_id:
        return _json_response({"error": "Impossible de désactiver son propre compte"}, status_code=400)
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    with SessionLocal() as sess:
        row = sess.execute(_t("UPDATE users SET is_active = NOT is_active WHERE id = :id RETURNING is_active"), {"id": user_id}).fetchone()
        sess.commit()
    if row is None:
        return _json_response({"error": "Utilisateur introuvable"}, status_code=404)
    return _json_response({"ok": True, "is_active": row[0]})


@app.post("/api/admin/invitations")
async def api_admin_create_invitation(request: Request) -> Response:
    from manamind.auth import require_admin, COOKIE_NAME
    admin = require_admin(mm_token=request.cookies.get(COOKIE_NAME))
    import uuid, secrets as _sec
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    token = _sec.token_urlsafe(32)
    with SessionLocal() as sess:
        row = sess.execute(_t("""
            INSERT INTO invitations (token, created_by, expires_at)
            VALUES (:token, :by, NOW() + INTERVAL '7 days')
            RETURNING token, expires_at
        """), {"token": token, "by": admin["id"]}).fetchone()
        sess.commit()
    base_url = str(request.base_url).rstrip("/")
    return _json_response({
        "ok": True,
        "token": row[0],
        "expires_at": row[1].isoformat(),
        "link": f"{base_url}/register?token={row[0]}",
    })


@app.get("/api/admin/invitations")
def api_admin_list_invitations(request: Request) -> Response:
    from manamind.auth import require_admin, COOKIE_NAME
    require_admin(mm_token=request.cookies.get(COOKIE_NAME))
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    with SessionLocal() as sess:
        rows = sess.execute(_t("""
            SELECT i.token, i.expires_at, i.used_at,
                   uc.email AS created_by_email,
                   uu.email AS used_by_email
            FROM invitations i
            LEFT JOIN users uc ON uc.id = i.created_by
            LEFT JOIN users uu ON uu.id = i.used_by
            ORDER BY i.created_at DESC LIMIT 50
        """)).fetchall()
    invs = []
    for r in rows:
        invs.append({
            "token": r.token,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "used_at": r.used_at.isoformat() if r.used_at else None,
            "created_by": r.created_by_email,
            "used_by": r.used_by_email,
        })
    return _json_response({"invitations": invs})


@app.get("/deck-select")
def deck_select_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_select.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/collection-manage")
def collection_manage_page() -> FileResponse:
    return FileResponse(
        ROOT / "collection_manage.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/collection")
def api_collection_list(
    request: Request,
    search: str = Query(""),
    sort: str = Query("name"),
    in_deck: str = Query(""),
) -> JSONResponse:
    """Liste les cartes de la collection avec filtre, tri et info decks."""
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.user_decks import load_config_for_user, get_deck_cards
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal

    decks = load_config_for_user(user["id"])
    cards_in_decks: dict[str, list[str]] = {}
    for deck in decks:
        commander = deck.get("commander", "")
        entries = get_deck_cards(user["id"], commander)
        for card_name, _qty in entries:
            norm = card_name.strip().lower()
            if norm not in cards_in_decks:
                cards_in_decks[norm] = []
            cards_in_decks[norm].append(commander)

    with SessionLocal() as sess:
        rows = sess.execute(_t("""
            WITH best_print AS (
                SELECT DISTINCT ON (LOWER(TRIM(sc2.name)))
                       LOWER(TRIM(sc2.name)) AS name_lower,
                       p.scryfall_id,
                       p.image_normal
                FROM scryfall_card_printings p
                JOIN scryfall_cards sc2 ON sc2.id = p.card_id
                WHERE p.lang = 'en' AND p.image_normal IS NOT NULL
                ORDER BY LOWER(TRIM(sc2.name)), p.released_at DESC
            )
            SELECT uc.card_name, uc.quantity,
                   sc.type_line, sc.mana_cost, sc.mana_value,
                   bp.image_normal AS image_url,
                   bp.scryfall_id
            FROM user_collection uc
            LEFT JOIN scryfall_cards sc ON LOWER(TRIM(sc.name)) = LOWER(TRIM(uc.card_name))
            LEFT JOIN best_print bp ON bp.name_lower = LOWER(TRIM(uc.card_name))
            WHERE uc.user_id = :uid
              AND (:search = '' OR LOWER(uc.card_name) LIKE LOWER(:search_like))
            ORDER BY uc.card_name ASC
        """), {"uid": user["id"], "search": search, "search_like": f"%{search}%"}).fetchall()

    result = []
    for r in rows:
        norm = r.card_name.strip().lower()
        decks_containing = cards_in_decks.get(norm, [])
        has_deck = len(decks_containing) > 0

        if in_deck == "yes" and not has_deck:
            continue
        if in_deck == "no" and has_deck:
            continue

        result.append({
            "card_name": r.card_name,
            "quantity": r.quantity,
            "type_line": r.type_line or "",
            "mana_cost": r.mana_cost or "",
            "mana_value": r.mana_value,
            "image_url": r.image_url,
            "scryfall_id": r.scryfall_id,
            "in_decks": decks_containing,
        })

    if sort == "name":
        result.sort(key=lambda x: x["card_name"].lower())

    return _json_response({"cards": result, "total": len(result)})


@app.patch("/api/collection/{card_name}")
async def api_collection_update(card_name: str, request: Request) -> JSONResponse:
    """Met à jour la quantité d'une carte dans la collection."""
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    body = await request.json()
    qty = int(body.get("quantity", 0))

    with SessionLocal() as sess:
        if qty <= 0:
            sess.execute(_t("DELETE FROM user_collection WHERE user_id = :uid AND LOWER(TRIM(card_name)) = LOWER(TRIM(:name))"), {"uid": user["id"], "name": card_name})
        else:
            sess.execute(_t("UPDATE user_collection SET quantity = :qty WHERE user_id = :uid AND LOWER(TRIM(card_name)) = LOWER(TRIM(:name))"), {"qty": qty, "uid": user["id"], "name": card_name})
        sess.commit()

    return _json_response({"ok": True, "card_name": card_name, "quantity": qty})


@app.delete("/api/collection/{card_name}")
def api_collection_delete(card_name: str, request: Request) -> JSONResponse:
    """Supprime une carte de la collection."""
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal

    with SessionLocal() as sess:
        sess.execute(_t("DELETE FROM user_collection WHERE user_id = :uid AND LOWER(TRIM(card_name)) = LOWER(TRIM(:name))"), {"uid": user["id"], "name": card_name})
        sess.commit()

    return _json_response({"ok": True})


@app.get("/{filename:path}")
def static_file(filename: str) -> FileResponse:
    file_path = ROOT / filename
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    return JSONResponse({"error": "Not found"}, status_code=404)


if __name__ == "__main__":
    import sys as _sys
    import uvicorn
    # workers > 1 requiert fork (Linux/macOS uniquement — pas supporté sur Windows)
    _workers = 4 if _sys.platform != "win32" else 1
    uvicorn.run("server:app", host="0.0.0.0", port=8080, reload=False, workers=_workers)
