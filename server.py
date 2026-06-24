#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import re
import unicodedata

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

app = FastAPI()

# ── Deck Improvement Engine — singleton lazy-loadé ────────────────────────────
# Chargé à la première requête POST /api/deck/analyze (~24s la première fois).
# Les appels suivants utilisent l'instance déjà en mémoire.
_deck_engine = None
_deck_engine_lock = None

def _get_deck_engine():
    """Retourne le DeckImprovementEngine en le chargeant si nécessaire (lazy)."""
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
        ROOT / "recommendations_view_slide16.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/cards")
def cards_page() -> FileResponse:
    return FileResponse(
        ROOT / "cards.html",
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

    try:
        import time as _time
        t0 = _time.perf_counter()

        profile   = engine.analyze_deck(commander, decklist)
        gap_summaries, dist_meta = engine.gap_analysis(commander, profile)
        additions = engine.generate_additions(commander, decklist, profile, gap_summaries)
        cuts      = engine.generate_cuts(commander, decklist, profile)
        replacements = engine.generate_replacements(cuts, additions)

        global_score = round(
            0.40 * profile.coherence_score
            + 0.35 * profile.avg_cosine_to_commander
            + 0.25 * max(0.0, 1.0 - dist_meta),
            4,
        )

        elapsed = round(_time.perf_counter() - t0, 2)

        # Sérialiser les clusters (top 10 pour le profil stratégique)
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

        # Gap analysis (clusters avec delta significatif)
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
            if abs(s.delta) > 0.015
        ][:20]

        return _json_response({
            "commander":     commander,
            "deck_score":    global_score,
            "deck_score_100": round(global_score * 100),
            "coherence":     profile.coherence_score,
            "distance_meta": dist_meta,
            "elapsed_s":     elapsed,
            "profile": {
                "card_count":         profile.card_count,
                "family_distribution": profile.family_distribution,
                "mana_curve":         profile.mana_curve,
                "color_distribution": profile.color_distribution,
                "cluster_profile":    cluster_profile,
                "missing_in_corpus":  profile.missing_in_corpus[:10],
            },
            "gap": gap_data,
            "top_additions": [
                {
                    "rank":            i + 1,
                    "card_name":       a.card_name,
                    "addition_score":  a.addition_score,
                    "score_100":       round(a.addition_score * 100),
                    "predicted_ir":    a.predicted_ir,
                    "cluster_name":    a.cluster_name,
                    "cluster_family":  a.cluster_family,
                    "gap_bonus":       a.cluster_gap_bonus,
                    "deck_synergy":    a.deck_synergy,
                    "explanation":     a.explanation,
                }
                for i, a in enumerate(additions)
            ],
            "top_cuts": [
                {
                    "rank":       i + 1,
                    "card_name":  c.card_name,
                    "cut_score":  c.cut_score,
                    "score_100":  round(c.cut_score * 100),
                    "reasons":    c.reasons,
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
        })

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


# ── API recherche de cartes ───────────────────────────────────────────────────
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
                # Tri par popularité EDHREC (1 = plus populaire), sans rank en dernier
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

    # 1. DB locale — recherche exacte sur le nom normalisé
    if _DB_AVAILABLE:
        try:
            with SessionLocal() as session:
                from sqlalchemy import func as _func
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
                    return _json_response({"url": row[0]})
        except Exception:
            pass

    # 2. Fallback Scryfall (requête serveur → pas de CORS)
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
                if url:
                    return _json_response({"url": url})
    except Exception:
        pass

    return _json_response({"url": None}, status_code=404)


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


@app.get("/collection-suggest")
def collection_suggest_page() -> FileResponse:
    return FileResponse(
        ROOT / "collection_suggest.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/collection-suggest")
def api_collection_suggest(
    top: int = Query(default=40, ge=1, le=100),
    commander: str | None = Query(default=None),
) -> JSONResponse:
    """
    Analyse la collection (Ma collection.txt), les decks existants (data/My decks/)
    et retourne les cartes disponibles ayant le meilleur taux d'inclusion
    dans les commandants de My_commanders.txt.
    Si commander est fourni, limite l'analyse à ce commandant.
    """
    from manamind.collection_advisor import suggest_from_collection
    result = suggest_from_collection(top_n=top, commander_filter=commander or None)
    return _json_response(result)


@app.get("/api/deck-composition")
def api_deck_composition(
    commander: str = Query(...),
) -> JSONResponse:
    """
    Retourne la composition par type du deck personnel et la moyenne EDHREC
    pour le commandant donné.
    """
    from manamind.collection_advisor import compute_deck_composition
    result = compute_deck_composition(commander)
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
    top: int = Query(default=60, ge=1, le=100),
) -> JSONResponse:
    """
    Retourne les cartes présentes dans un deck mais qui auraient un meilleur taux
    d'inclusion dans un autre deck du même joueur, classées par gain décroissant.
    """
    from manamind.collection_advisor import suggest_moves
    result = suggest_moves(top_n=top)
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
    commander: str = Query(..., description="Nom exact du commandant"),
) -> JSONResponse:
    """
    Retourne les cartes du deck du commandant triées par inclusion_rate croissant.
    Les candidates à la coupe sont en tête de liste.
    """
    from manamind.collection_advisor import suggest_cuts
    result = suggest_cuts(commander_name=commander)
    return _json_response(result)


@app.get("/api/my-decks")
def api_my_decks() -> JSONResponse:
    """
    Retourne la liste des decks personnels (data/My decks/) avec leur nom de commandant
    et leur contenu (lignes brutes) pour pré-remplir le textarea de deck-suggest.
    """
    from manamind.collection_advisor import MY_DECKS_DIR, load_allowed_commanders, _find_deck_file

    commanders = load_allowed_commanders()  # {norm: display}
    # Construire mapping fichier -> commandant display
    file_to_cmd: dict[str, str] = {}
    for cmd_norm, cmd_display in commanders.items():
        f = _find_deck_file(cmd_display)
        if f:
            file_to_cmd[f.name] = cmd_display

    decks = []
    if MY_DECKS_DIR.exists():
        for f in MY_DECKS_DIR.glob("*.txt"):
            commander = file_to_cmd.get(f.name, f.stem)
            content = f.read_text(encoding="utf-8", errors="replace")
            decks.append({"commander": commander, "filename": f.name, "content": content})

    decks.sort(key=lambda d: d["commander"].lower())
    return _json_response({"decks": decks})


@app.get("/deck-suggest")
def deck_suggest_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_suggest.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/deck-build")
def deck_build_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_build.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.post("/api/deck-suggest")
async def api_deck_suggest(request: Request) -> JSONResponse:
    """
    Détecte le commandant le plus probable depuis une liste de cartes et retourne
    les 20 cartes absentes de la liste avec le meilleur taux d'inclusion.

    Body JSON : { "cards": ["Sol Ring", "Cultivate", ...] }
    """
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "JSON invalide"}, status_code=400)

    cards = body.get("cards") or []
    if not isinstance(cards, list) or len(cards) == 0:
        return _json_response({"error": "Liste de cartes vide ou invalide"}, status_code=400)

    import re as _re
    def _parse_card_name(raw: str) -> str:
        # Retire " (SET) #num" (format Moxfield)
        raw = _re.sub(r'\s*\([A-Z0-9]+\)\s*#\S+$', '', raw.strip())
        # Retire quantité en début "1x " ou "2 "
        raw = _re.sub(r'^\d+[xX]?\s+', '', raw)
        return raw.strip()

    cards = [_parse_card_name(str(c)) for c in cards if _parse_card_name(str(c))]

    from manamind.card_commander_matcher import suggest_additions
    result = suggest_additions(cards, top_n=20)
    return _json_response(result)


@app.get("/commander-suggest")
def commander_suggest_page() -> FileResponse:
    return FileResponse(
        ROOT / "commander_suggest.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/commander-suggest")
def api_commander_suggest(
    card: str = Query(..., description="Nom de la carte à rechercher"),
    top: int = Query(default=3, ge=1, le=10),
) -> JSONResponse:
    """
    Retourne les commandants (parmi data/commanders.txt) qui jouent le plus souvent
    la carte donnée, triés par taux d'inclusion décroissant.
    """
    from manamind.card_commander_matcher import suggest_commanders

    card = card.strip()
    if not card:
        return _json_response({"error": "Paramètre 'card' manquant"}, status_code=400)

    results = suggest_commanders(card, top_n=top)
    return _json_response({"card": card, "suggestions": results})


@app.get("/{filename:path}")
def static_file(filename: str) -> FileResponse:
    file_path = ROOT / filename
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    return JSONResponse({"error": "Not found"}, status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8080, reload=True)
