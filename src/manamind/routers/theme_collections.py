"""Router des collections à thème, réservées pour l'instant à l'administrateur."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from manamind import theme_collections as themes
from manamind.auth import COOKIE_NAME, require_admin
from manamind.db.engine import SessionLocal
from manamind.routers._shared import _json_response

router = APIRouter()


def _admin(request: Request) -> dict:
    return require_admin(mm_token=request.cookies.get(COOKIE_NAME))


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


def _bad(message: str) -> Response:
    return _json_response({"error": message}, status_code=400)


def _missing() -> Response:
    return _json_response({"error": "Collection introuvable"}, status_code=404)


@router.get("/api/v2/themes")
def api_list(request: Request) -> Response:
    """Les collections, avec leur progression, leur valeur et leur couverture."""
    user = _admin(request)
    return _json_response({"collections": themes.list_collections(user["id"])})


@router.get("/api/v2/themes/functions")
def api_functions(request: Request) -> Response:
    """Les fonctions de cartes les plus courantes, pour aider la saisie."""
    from sqlalchemy import text

    _admin(request)
    with SessionLocal() as session:
        names = [r[0] for r in session.execute(text("""
            SELECT tag_name FROM scryfall_card_tags
            GROUP BY tag_name ORDER BY COUNT(*) DESC LIMIT 400
        """))]
    return _json_response({"functions": sorted(names)})


@router.post("/api/v2/themes/preview")
async def api_preview(request: Request) -> Response:
    """Ce que donneraient des critères, avant d'enregistrer."""
    user = _admin(request)
    body = await _body(request)
    try:
        definition = themes.clean_definition({**body, "name": body.get("name") or "aperçu"})
        return _json_response(themes.preview(user["id"], definition))
    except themes.CriteriaError as exc:
        return _bad(str(exc))


@router.post("/api/v2/themes")
async def api_create(request: Request) -> Response:
    user = _admin(request)
    try:
        definition = themes.clean_definition(await _body(request))
        # Une collection trop large se refuse dès l'enregistrement.
        themes.preview(user["id"], definition)
    except themes.CriteriaError as exc:
        return _bad(str(exc))
    return _json_response({"id": themes.create(user["id"], definition)})


@router.get("/api/v2/themes/{collection_id}")
def api_get(collection_id: int, request: Request) -> Response:
    """Une collection : ses critères et ses éditions, groupées par carte."""
    user = _admin(request)
    with SessionLocal() as session:
        definition = themes.get(session, user["id"], collection_id)
        if definition is None:
            return _missing()
        try:
            data = themes.evaluate(session, user["id"], definition, collection_id)
        except themes.CriteriaError as exc:
            return _json_response({**definition, "error": str(exc), "cards": [], "stats": None})
    return _json_response({**definition, **data})


@router.put("/api/v2/themes/{collection_id}")
async def api_update(collection_id: int, request: Request) -> Response:
    user = _admin(request)
    try:
        definition = themes.clean_definition(await _body(request))
        themes.preview(user["id"], definition)
    except themes.CriteriaError as exc:
        return _bad(str(exc))
    if not themes.update(user["id"], collection_id, definition):
        return _missing()
    return _json_response({"ok": True})


@router.delete("/api/v2/themes/{collection_id}")
def api_delete(collection_id: int, request: Request) -> Response:
    user = _admin(request)
    if not themes.delete(user["id"], collection_id):
        return _missing()
    return _json_response({"ok": True})


@router.post("/api/v2/themes/{collection_id}/duplicate")
def api_duplicate(collection_id: int, request: Request) -> Response:
    user = _admin(request)
    new_id = themes.duplicate(user["id"], collection_id)
    if new_id is None:
        return _missing()
    return _json_response({"id": new_id})


@router.post("/api/v2/themes/{collection_id}/complete/{card_id}")
def api_mark_complete(collection_id: int, card_id: int, request: Request) -> Response:
    """« Je possède toutes les éditions » de cette carte, dans cette collection."""
    user = _admin(request)
    if not themes.set_complete(user["id"], collection_id, card_id, True):
        return _missing()
    return _json_response({"ok": True})


@router.delete("/api/v2/themes/{collection_id}/complete/{card_id}")
def api_unmark_complete(collection_id: int, card_id: int, request: Request) -> Response:
    user = _admin(request)
    if not themes.set_complete(user["id"], collection_id, card_id, False):
        return _missing()
    return _json_response({"ok": True})


@router.post("/api/v2/themes/{collection_id}/buylist")
async def api_to_buylist(collection_id: int, request: Request) -> Response:
    """Ajoute à la buylist les cartes nommées, ou toutes celles qui manquent.

    La buylist retient des cartes, pas des éditions : une carte dont il manque
    trois éditions n'y entre qu'une fois.
    """
    from manamind.buylist import pin_card

    user = _admin(request)
    body = await _body(request)
    names = body.get("names")
    if not names:
        with SessionLocal() as session:
            definition = themes.get(session, user["id"], collection_id)
            if definition is None:
                return _missing()
            try:
                data = themes.evaluate(session, user["id"], definition, collection_id)
            except themes.CriteriaError as exc:
                return _bad(str(exc))
        names = [card["name"] for card in data["cards"]
                 if any(not e["owned"] for e in card["editions"])]
    added = [name for name in (pin_card(user["id"], str(n)) for n in names[:2000]) if name]
    return _json_response({"added": len(added)})
