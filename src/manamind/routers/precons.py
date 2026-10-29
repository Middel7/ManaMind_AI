"""Router des decks préconstruits : la liste et l'ajout en un clic."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from manamind.auth import COOKIE_NAME, get_current_user
from manamind.routers._shared import _json_response, limiter

router = APIRouter()


def _user(request: Request) -> dict:
    return get_current_user(mm_token=request.cookies.get(COOKIE_NAME))


@router.get("/api/precons")
def api_list_precons(request: Request) -> Response:
    """Tous les précons Commander, du plus récent au plus ancien."""
    from manamind.precons import list_precons

    user = _user(request)
    return _json_response({"precons": list_precons(user["id"])})


@router.post("/api/precons/{precon_id}/add")
# Chaque ajout écrit une centaine de lignes, et jusqu'à autant dans la
# collection : un clic répété par erreur ne doit pas en multiplier les copies.
@limiter.limit("10/minute")
async def api_add_precon(precon_id: str, request: Request) -> Response:
    """Crée un deck du joueur à partir d'un précon."""
    from manamind.precons import add_precon

    user = _user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = add_precon(user["id"], precon_id,
                        add_to_collection=bool(body.get("add_to_collection")))
    if result is None:
        return _json_response({"error": "Deck préconstruit introuvable"}, status_code=404)
    return _json_response({"ok": True, **result})
