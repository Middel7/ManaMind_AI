"""Router pour les pages HTML et routes admin.

L'arborescence est organisee autour des deux objets que manipule l'utilisateur
— sa collection et ses decks — plutot que par outil. Les anciennes URL, nommees
d'apres les outils, redirigent en permanent vers les nouvelles.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from manamind.routers._shared import ROOT, _json_response

router = APIRouter()

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


def _page(filename: str) -> FileResponse:
    return FileResponse(ROOT / filename, media_type="text/html", headers=_NO_CACHE)


# ── Collection ────────────────────────────────────────────────────────────────

@router.get("/collection")
def page_collection() -> FileResponse:
    return _page("collection.html")


@router.get("/collection/import")
def page_collection_import() -> FileResponse:
    return _page("collection_import.html")


@router.get("/collection/ajout")
def page_collection_add() -> FileResponse:
    return _page("collection_add.html")


@router.get("/collection/boosters")
def page_collection_boosters() -> FileResponse:
    return _page("collection_boosters.html")


@router.get("/collection/commandants")
def page_collection_commanders() -> FileResponse:
    return _page("collection_commanders.html")


@router.get("/collection/commandants/{commander}")
def page_collection_commander_build(commander: str) -> FileResponse:
    return _page("collection_commander_build.html")


# ── Decks ─────────────────────────────────────────────────────────────────────
# Les routes litterales sont declarees avant /decks/{deck_id}, sinon elles
# seraient capturees comme des identifiants.

@router.get("/decks")
def page_decks() -> FileResponse:
    return _page("decks.html")


@router.get("/decks/ameliorer")
def page_deck_improve() -> FileResponse:
    return _page("deck_improve.html")


@router.get("/decks/alleger")
def page_deck_trim() -> FileResponse:
    return _page("deck_trim.html")


@router.get("/decks/commandant")
def page_deck_swap() -> FileResponse:
    return _page("deck_swap.html")


@router.get("/decks/deplacements")
def page_deck_moves() -> FileResponse:
    return _page("deck_moves.html")


@router.get("/decks/analyse")
def page_deck_analyze() -> FileResponse:
    return _page("deck_analyze.html")


@router.get("/decks/{deck_id}")
def page_deck_detail(deck_id: str) -> FileResponse:
    return _page("deck_detail.html")


# ── Cartes, analyse, profil ───────────────────────────────────────────────────

@router.get("/cartes/commandant")
def page_card_commander() -> FileResponse:
    return _page("card_commander.html")


@router.get("/analyse")
def page_analysis() -> FileResponse:
    return _page("results.html")


@router.get("/profil")
def page_profile() -> FileResponse:
    return _page("profile.html")


@router.get("/admin")
def admin_page() -> FileResponse:
    return _page("admin.html")


# ── Redirections depuis l'ancienne arborescence ───────────────────────────────

_LEGACY_REDIRECTS = {
    "/results": "/analyse",
    "/collection-manage": "/collection",
    "/collection-commanders": "/collection/commandants",
    "/deck-config": "/collection/import",
    "/deck-edit": "/decks",
    "/deck-build": "/decks/ameliorer",
    "/deck-trim": "/decks/alleger",
    "/deck-moves": "/decks/deplacements",
    "/deck-select": "/decks/analyse",
    "/commander-suggest": "/cartes/commandant",
    "/commander-swap": "/decks/commandant",
}


def _make_redirect(target: str):
    def _redirect(request: Request) -> RedirectResponse:
        query = request.url.query
        return RedirectResponse(f"{target}?{query}" if query else target, status_code=301)
    return _redirect


for _old, _new in _LEGACY_REDIRECTS.items():
    router.add_api_route(_old, _make_redirect(_new), methods=["GET"], include_in_schema=False)


@router.get("/deck-edit/{deck_id}", include_in_schema=False)
def redirect_deck_edit_detail(deck_id: str) -> RedirectResponse:
    return RedirectResponse(f"/decks/{deck_id}", status_code=301)


# ── API admin ─────────────────────────────────────────────────────────────────

@router.get("/api/admin/users")
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


@router.post("/api/admin/users/{user_id}/toggle")
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


@router.post("/api/admin/invitations")
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


@router.get("/api/admin/invitations")
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
