"""Router d'authentification — routes /auth/* et pages /login, /register."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from manamind.routers._shared import ROOT, _SECURE_COOKIE, _json_response, limiter

router = APIRouter()


@router.get("/login")
def login_page() -> FileResponse:
    return FileResponse(ROOT / "login.html", media_type="text/html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@router.get("/register")
def register_page() -> FileResponse:
    return FileResponse(ROOT / "register.html", media_type="text/html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@router.post("/auth/login")
@limiter.limit("10/minute")
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
        httponly=True, samesite="lax", secure=_SECURE_COOKIE,
        max_age=EXPIRE_DAYS * 86400,
    )
    return resp


@router.post("/auth/logout")
def auth_logout() -> Response:
    from manamind.auth import COOKIE_NAME
    resp = _json_response({"ok": True})
    resp.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=_SECURE_COOKIE,
        path="/",
    )
    return resp


@router.post("/auth/register")
@limiter.limit("5/minute")
async def auth_register(request: Request) -> Response:
    from manamind.auth import register_with_invitation, create_token, COOKIE_NAME, EXPIRE_DAYS
    from fastapi import HTTPException
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

    # Créer le compte atomiquement (validation invitation + création user dans une seule transaction)
    try:
        user_id, dn = register_with_invitation(token, email, password, display_name or None)
    except HTTPException as e:
        return _json_response({"error": e.detail}, status_code=e.status_code)

    jwt_token = create_token(user_id)
    resp = _json_response({"ok": True, "display_name": dn})
    resp.set_cookie(
        key=COOKIE_NAME, value=jwt_token,
        httponly=True, samesite="lax", secure=_SECURE_COOKIE,
        max_age=EXPIRE_DAYS * 86400,
    )
    return resp


@router.get("/auth/me")
@limiter.limit("60/minute")
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
