"""Router pour les pages HTML et routes admin."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from manamind.routers._shared import ROOT, _json_response

router = APIRouter()


@router.get("/results")
def results_page() -> FileResponse:
    return FileResponse(
        ROOT / "results.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/deck-moves")
def deck_moves_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_moves.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/deck-trim")
def deck_trim_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_trim.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/deck-edit")
def deck_edit_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_edit.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/deck-edit/{deck_id}")
def deck_edit_detail_page(deck_id: str) -> FileResponse:
    return FileResponse(
        ROOT / "deck_edit_detail.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/deck-config")
def deck_config_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_config.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/deck-build")
def deck_build_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_build.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/commander-suggest")
def commander_suggest_page() -> FileResponse:
    return FileResponse(
        ROOT / "commander_suggest.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/collection-commanders")
def page_collection_commanders() -> FileResponse:
    return FileResponse(ROOT / "collection_commanders.html")


@router.get("/deck-select")
def deck_select_page() -> FileResponse:
    return FileResponse(
        ROOT / "deck_select.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/collection-manage")
def collection_manage_page() -> FileResponse:
    return FileResponse(
        ROOT / "collection_manage.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/admin")
def admin_page() -> FileResponse:
    return FileResponse(ROOT / "admin.html", media_type="text/html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


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
