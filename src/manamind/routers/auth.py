"""Router d'authentification — routes /auth/* et pages publiques.

Les pages servies ici sont les seules que l'on ouvre sans être connecté :
connexion, inscription, mot de passe oublié et mentions légales.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from manamind.routers._shared import ROOT, _SECURE_COOKIE, _json_response, limiter

router = APIRouter()

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}

_PASSWORD_MIN = 8

# Réponse identique, que l'adresse ait un compte ou non : sans elle, le
# formulaire servirait à vérifier qui est inscrit.
_FORGOT_REPLY = ("Si un compte existe pour cette adresse, un lien de "
                 "réinitialisation vient de lui être envoyé.")


def _set_session_cookie(resp: Response, user_id: int) -> None:
    from manamind.auth import create_token, COOKIE_NAME, EXPIRE_DAYS
    resp.set_cookie(
        key=COOKIE_NAME, value=create_token(user_id),
        httponly=True, samesite="lax", secure=_SECURE_COOKIE,
        max_age=EXPIRE_DAYS * 86400,
    )


@router.get("/mot-de-passe")
def password_page() -> FileResponse:
    return FileResponse(ROOT / "password.html", media_type="text/html", headers=_NO_CACHE)


@router.get("/mentions-legales")
def legal_page() -> FileResponse:
    return FileResponse(ROOT / "legal.html", media_type="text/html", headers=_NO_CACHE)


@router.get("/auth/options")
def auth_options() -> Response:
    """Ce que les pages publiques doivent savoir avant d'afficher un formulaire.

    L'inscription libre et l'envoi d'e-mails se règlent dans le .env du
    serveur : les pages les lisent ici plutôt que de les supposer.
    """
    from manamind.auth import registrations_open
    from manamind.mail import mail_configured
    return _json_response({
        "registrations_open": registrations_open(),
        "mail_enabled": mail_configured(),
        "contact_email": os.environ.get("CONTACT_EMAIL", ""),
    })


@router.get("/login")
def login_page() -> FileResponse:
    return FileResponse(ROOT / "login.html", media_type="text/html", headers=_NO_CACHE)


@router.get("/register")
def register_page() -> FileResponse:
    return FileResponse(ROOT / "register.html", media_type="text/html", headers=_NO_CACHE)


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
# Deux plafonds : la minute freine une rafale, la journée borne ce qu'une même
# adresse IP peut créer une fois les inscriptions ouvertes à tous.
@limiter.limit("5/minute;20/day")
async def auth_register(request: Request) -> Response:
    from manamind.auth import register_open, register_with_invitation, registrations_open
    from fastapi import HTTPException
    body = await request.json()
    token        = (body.get("token") or "").strip()
    email        = (body.get("email") or "").strip().lower()
    password     = body.get("password") or ""
    display_name = (body.get("display_name") or "").strip()[:40]

    # Champ piège, caché aux humains : un robot qui remplit tout le formulaire
    # le renseigne. On lui répond comme à un humain, sans rien créer.
    if (body.get("website") or "").strip():
        return _json_response({"ok": True, "display_name": display_name})

    if not token and not registrations_open():
        return _json_response({"error": "Lien d'invitation manquant"}, status_code=400)
    if not email or "@" not in email or len(email) > 254:
        return _json_response({"error": "Email invalide"}, status_code=400)
    if len(password) < _PASSWORD_MIN:
        return _json_response({"error": "Mot de passe trop court (8 caractères min)"}, status_code=400)

    # Avec une invitation, elle est consommée dans la même transaction que la
    # création du compte ; sans, c'est l'inscription libre et son plafond.
    try:
        if token:
            user_id, dn = register_with_invitation(token, email, password, display_name or None)
        else:
            user_id, dn = register_open(email, password, display_name or None)
    except HTTPException as e:
        return _json_response({"error": e.detail}, status_code=e.status_code)

    resp = _json_response({"ok": True, "display_name": dn})
    _set_session_cookie(resp, user_id)
    return resp


@router.post("/auth/password/forgot")
@limiter.limit("3/minute;10/hour")
async def auth_password_forgot(request: Request) -> Response:
    """Envoie un lien de réinitialisation, si l'adresse a un compte."""
    from manamind.auth import RESET_EXPIRE_MINUTES, create_password_reset
    from manamind.mail import mail_configured, send_mail

    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return _json_response({"error": "Adresse e-mail invalide"}, status_code=400)
    if not mail_configured():
        return _json_response(
            {"error": "L'envoi d'e-mails n'est pas activé : contactez l'administrateur."},
            status_code=503)

    prepared = create_password_reset(email)
    if prepared is not None:
        token, account_email = prepared
        base_url = str(request.base_url).rstrip("/")
        link = f"{base_url}/mot-de-passe?token={token}"
        send_mail(
            account_email,
            "ManaMind — réinitialiser votre mot de passe",
            "Bonjour,\n\n"
            "Une réinitialisation du mot de passe de votre compte ManaMind a été "
            "demandée. Pour choisir un nouveau mot de passe, ouvrez ce lien :\n\n"
            f"{link}\n\n"
            f"Il reste valable {RESET_EXPIRE_MINUTES} minutes et ne sert qu'une fois.\n\n"
            "Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail : "
            "votre mot de passe actuel reste inchangé.\n",
        )
    return _json_response({"ok": True, "message": _FORGOT_REPLY})


@router.post("/auth/password/reset")
@limiter.limit("10/minute")
async def auth_password_reset(request: Request) -> Response:
    """Choisit un nouveau mot de passe à partir d'un lien reçu par e-mail."""
    from manamind.auth import consume_password_reset
    from fastapi import HTTPException

    body = await request.json()
    token = (body.get("token") or "").strip()
    password = body.get("password") or ""
    if not token:
        return _json_response({"error": "Lien de réinitialisation manquant"}, status_code=400)
    if len(password) < _PASSWORD_MIN:
        return _json_response({"error": "Mot de passe trop court (8 caractères min)"}, status_code=400)

    try:
        user_id = consume_password_reset(token, password)
    except HTTPException as e:
        return _json_response({"error": e.detail}, status_code=e.status_code)

    # Le lien prouve la possession de la boîte : on ouvre la session dans la
    # foulée, plutôt que de renvoyer vers un formulaire de connexion.
    resp = _json_response({"ok": True})
    _set_session_cookie(resp, user_id)
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
