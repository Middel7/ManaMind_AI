"""Router commander-swap — route /api/commander-swap.

Propose des commandants alternatifs pour une decklist existante, classés selon
la valeur en euros des cartes qu'ils conserveraient.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from manamind.routers._shared import _json_response, limiter

router = APIRouter()


@router.get("/api/commander-swap")
@limiter.limit("20/minute")
def api_commander_swap(
    request: Request,
    commander: str = Query(..., description="Commandant actuel du deck à analyser"),
    top: int = Query(default=10, ge=1, le=25),
    sort: str = Query(default="value", description="value | affinity"),
    max_colors: int = Query(default=5, ge=1, le=5),
    staple_threshold: float = Query(default=40.0, ge=0.0, le=100.0),
    min_inclusion: float = Query(default=10.0, ge=0.0, le=100.0),
    missing_min_inclusion: float = Query(default=50.0, ge=0.0, le=100.0),
) -> Response:
    """
    Classe les commandants qui tireraient le meilleur parti des cartes de ce deck.

    - sort="value"    : valeur EUR conservée (brut) ;
    - sort="affinity" : valeur EUR pondérée par le taux d'inclusion réel ;
    - max_colors      : limite le nombre de couleurs de l'identité proposée ;
    - staple_threshold: au-delà de ce % de présence tous decks confondus, une
                        carte est neutre et sort du score ;
    - min_inclusion   : en deçà de ce % de présence dans les decks du candidat,
                        la carte ne compte pas comme conservée ;
    - missing_min_inclusion : au-delà de ce % de présence, une carte absente du
                        deck est proposée à l'achat (top 10 par prix).
    """
    from manamind.auth import COOKIE_NAME, get_current_user
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))

    commander = (commander or "").strip()
    if not commander:
        return _json_response({"error": "Commandant manquant."}, status_code=400)

    try:
        from manamind.commander_swap import suggest_swaps
        result = suggest_swaps(
            user_id=user["id"],
            commander=commander,
            top=top,
            staple_threshold=staple_threshold,
            sort=sort,
            max_colors=max_colors,
            min_inclusion=min_inclusion,
            missing_min_inclusion=missing_min_inclusion,
        )
    except Exception as exc:  # base indisponible, SQL en erreur…
        return _json_response({"error": f"Erreur d'analyse : {exc}"}, status_code=500)

    return _json_response(result)
