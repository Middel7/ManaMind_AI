"""Router moteur IA — routes /api/engine/*, /api/deck/analyze, /api/deck/explanation."""
from __future__ import annotations

import traceback as _traceback_mod

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from manamind.routers._shared import _DEBUG, _json_response, limiter

router = APIRouter()

# _get_deck_engine est importé depuis server.py au moment de l'appel
# pour éviter les imports circulaires. On l'importe ici via une fonction d'accès.


def _get_engine():
    """Accès au singleton DeckImprovementEngine défini dans server.py."""
    import server
    return server._get_deck_engine()


def _engine_ready() -> bool:
    """Vérifie si le moteur est déjà chargé (sans le charger)."""
    import server
    return server._deck_engine is not None


@router.get("/api/engine/status")
def api_engine_status() -> JSONResponse:
    """Retourne l'état de chargement du DeckImprovementEngine."""
    ready = _engine_ready()
    return _json_response({"ready": ready})


@router.post("/api/engine/start")
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

    if _engine_ready():
        return _json_response({"status": "already_ready"})

    def _load():
        try:
            _get_engine()
        except Exception as exc:
            import logging
            logging.getLogger("manamind").warning(f"Chargement engine échoué : {exc}")

    threading.Thread(target=_load, daemon=True, name="engine-manual-start").start()
    return _json_response({"status": "starting"})


@router.post("/api/deck/analyze")
@limiter.limit("10/minute")
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
    from manamind.auth import get_current_user, COOKIE_NAME
    from fastapi import HTTPException
    try:
        _user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    except HTTPException:
        return _json_response({"error": "Authentification requise"}, status_code=401)
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
        engine = _get_engine()
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
        resp_500 = {"error": f"Erreur analyse : {exc}"}
        if _DEBUG:
            resp_500["detail"] = _traceback_mod.format_exc()
        return _json_response(resp_500, status_code=500)


@router.get("/api/deck/explanation")
@limiter.limit("10/minute")
async def api_deck_explanation(
    request: Request,
    commander: str = Query(..., description="Nom du commandant"),
    card:      str = Query(..., description="Nom de la carte"),
) -> JSONResponse:
    """
    Explication détaillée de la recommandation d'une carte pour un commandant.
    Calcule les 4 signaux hybrides et retourne une explication en langage naturel.
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    from fastapi import HTTPException
    try:
        _user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    except HTTPException:
        return _json_response({"error": "Authentification requise"}, status_code=401)
    try:
        engine = _get_engine()
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
