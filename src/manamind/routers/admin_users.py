"""Router admin — gestion des comptes utilisateurs et des invitations.

Les garde-fous de ce module partent tous du même principe : l'administration
ne doit jamais pouvoir se verrouiller elle-même dehors. Un administrateur ne
peut ni se rétrograder, ni se désactiver, ni se supprimer, et la dernière
personne capable d'administrer le site ne peut pas perdre ce pouvoir.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import Row, text
from sqlalchemy.orm import Session

from manamind.auth import hash_password, require_admin
from manamind.db.engine import SessionLocal
from manamind.routers._shared import _json_response

router = APIRouter()

ROLES = ("admin", "user")

# Longueur du mot de passe engendré lors d'une réinitialisation. token_urlsafe
# produit environ 4 caractères pour 3 octets : 12 octets donnent 16 caractères,
# assez pour être transmis de vive voix sans être devinable.
_RESET_PASSWORD_BYTES = 12


def _count_other_active_admins(session: Session, user_id: int) -> int:
    """Nombre d'administrateurs actifs autres que `user_id`."""
    return session.execute(
        text("""
            SELECT count(*) FROM users
            WHERE role = 'admin' AND is_active AND id <> :id
        """),
        {"id": user_id},
    ).scalar_one()


def _fetch_user(session: Session, user_id: int) -> Row | None:
    return session.execute(
        text("SELECT id, email, role, is_active FROM users WHERE id = :id"),
        {"id": user_id},
    ).fetchone()


@router.get("/api/admin/users")
def api_admin_list_users(_admin: dict = Depends(require_admin)) -> Response:
    """Liste les comptes, avec de quoi juger de leur activité réelle."""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT u.id, u.email, u.display_name, u.role, u.is_active,
                   u.created_at, u.last_login_at,
                   (u.google_id IS NOT NULL) AS via_google,
                   COALESCE(c.cards, 0)  AS cards,
                   COALESCE(d.decks, 0)  AS decks
            FROM users u
            -- Sous-requêtes agrégées plutôt que deux LEFT JOIN directs : joindre
            -- les lignes de collection et de decks dans la même requête
            -- multiplierait les unes par les autres.
            LEFT JOIN (
                SELECT user_id, SUM(quantity) AS cards
                FROM user_collection GROUP BY user_id
            ) c ON c.user_id = u.id
            LEFT JOIN (
                SELECT user_id, COUNT(DISTINCT commander) AS decks
                FROM user_deck_cards GROUP BY user_id
            ) d ON d.user_id = u.id
            ORDER BY u.id
        """)).fetchall()

    return _json_response({
        "users": [{
            "id": r.id,
            "email": r.email,
            "display_name": r.display_name,
            "role": r.role,
            "is_active": bool(r.is_active),
            "via_google": bool(r.via_google),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "last_login_at": r.last_login_at.isoformat() if r.last_login_at else None,
            "cards": int(r.cards or 0),
            "decks": int(r.decks or 0),
        } for r in rows]
    })


@router.patch("/api/admin/users/{user_id}")
async def api_admin_update_user(
    user_id: int, request: Request, admin: dict = Depends(require_admin)
) -> Response:
    """Change le rôle et/ou l'état d'activation d'un compte."""
    body = await request.json()
    new_role = body.get("role")
    new_active = body.get("is_active")

    if new_role is None and new_active is None:
        return _json_response({"error": "Rien à modifier"}, status_code=400)
    if new_role is not None and new_role not in ROLES:
        return _json_response(
            {"error": f"Rôle inconnu : {new_role}"}, status_code=400)

    # Se rétrograder ou se désactiver soi-même ferme la porte de l'intérieur.
    if user_id == admin["id"]:
        if new_role is not None and new_role != "admin":
            return _json_response(
                {"error": "Vous ne pouvez pas retirer votre propre rôle d'administrateur."},
                status_code=400)
        if new_active is False:
            return _json_response(
                {"error": "Vous ne pouvez pas désactiver votre propre compte."},
                status_code=400)

    with SessionLocal() as session:
        user = _fetch_user(session, user_id)
        if user is None:
            return _json_response({"error": "Compte introuvable"}, status_code=404)

        # Le site doit toujours conserver au moins un administrateur actif.
        loses_admin = (new_role is not None and new_role != "admin") or new_active is False
        if user.role == "admin" and user.is_active and loses_admin:
            if _count_other_active_admins(session, user_id) == 0:
                return _json_response(
                    {"error": "C'est le dernier administrateur actif : "
                              "nommez-en un autre avant de modifier ce compte."},
                    status_code=400)

        sets, params = [], {"id": user_id}
        if new_role is not None:
            sets.append("role = :role")
            params["role"] = new_role
        if new_active is not None:
            sets.append("is_active = :active")
            params["active"] = bool(new_active)

        row = session.execute(
            text(f"UPDATE users SET {', '.join(sets)} WHERE id = :id "
                 "RETURNING id, email, role, is_active"),
            params,
        ).fetchone()
        session.commit()

    return _json_response({
        "ok": True,
        "user": {"id": row.id, "email": row.email,
                 "role": row.role, "is_active": bool(row.is_active)},
    })


@router.post("/api/admin/users/{user_id}/password")
def api_admin_reset_password(
    user_id: int, admin: dict = Depends(require_admin)
) -> Response:
    """Engendre un nouveau mot de passe et le renvoie une seule fois.

    Le mot de passe est tiré au sort côté serveur plutôt que saisi : il n'est
    stocké nulle part en clair, et la réponse est le seul endroit où il
    apparaît. À l'administrateur de le transmettre puis de l'oublier.
    """
    new_password = secrets.token_urlsafe(_RESET_PASSWORD_BYTES)

    with SessionLocal() as session:
        user = _fetch_user(session, user_id)
        if user is None:
            return _json_response({"error": "Compte introuvable"}, status_code=404)

        session.execute(
            text("UPDATE users SET password_hash = :pw WHERE id = :id"),
            {"pw": hash_password(new_password), "id": user_id},
        )
        session.commit()

    return _json_response({
        "ok": True,
        "email": user.email,
        "password": new_password,
    })


@router.delete("/api/admin/users/{user_id}")
async def api_admin_delete_user(
    user_id: int, request: Request, admin: dict = Depends(require_admin)
) -> Response:
    """Supprime définitivement un compte et tout ce qui lui appartient.

    Les tables user_* sont en ON DELETE CASCADE : la collection, les decks, les
    préférences d'édition et le profil partent avec le compte. L'appelant doit
    répéter l'adresse e-mail dans le corps de la requête, pour qu'une
    suppression ne puisse pas résulter d'un simple clic mal placé.
    """
    if user_id == admin["id"]:
        return _json_response(
            {"error": "Vous ne pouvez pas supprimer votre propre compte."},
            status_code=400)

    body = await request.json()
    confirm = (body.get("confirm_email") or "").strip().lower()

    with SessionLocal() as session:
        user = _fetch_user(session, user_id)
        if user is None:
            return _json_response({"error": "Compte introuvable"}, status_code=404)

        if confirm != user.email.lower():
            return _json_response(
                {"error": "L'adresse saisie ne correspond pas à ce compte."},
                status_code=400)

        if user.role == "admin" and user.is_active:
            if _count_other_active_admins(session, user_id) == 0:
                return _json_response(
                    {"error": "C'est le dernier administrateur actif : "
                              "nommez-en un autre avant de supprimer ce compte."},
                    status_code=400)

        session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        session.commit()

    return _json_response({"ok": True, "email": user.email})


@router.post("/api/admin/invitations")
def api_admin_create_invitation(
    request: Request, admin: dict = Depends(require_admin)
) -> Response:
    """Cree un lien d'inscription valable sept jours et utilisable une fois."""
    token = secrets.token_urlsafe(32)
    with SessionLocal() as session:
        row = session.execute(text("""
            INSERT INTO invitations (token, created_by, expires_at)
            VALUES (:token, :by, NOW() + INTERVAL '7 days')
            RETURNING token, expires_at
        """), {"token": token, "by": admin["id"]}).fetchone()
        session.commit()

    base_url = str(request.base_url).rstrip("/")
    return _json_response({
        "ok": True,
        "token": row.token,
        "expires_at": row.expires_at.isoformat(),
        "link": f"{base_url}/register?token={row.token}",
    })


@router.get("/api/admin/invitations")
def api_admin_list_invitations(_admin: dict = Depends(require_admin)) -> Response:
    """Les cinquante dernieres invitations, utilisees ou non."""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT i.token, i.expires_at, i.used_at,
                   uc.email AS created_by_email,
                   uu.email AS used_by_email
            FROM invitations i
            LEFT JOIN users uc ON uc.id = i.created_by
            LEFT JOIN users uu ON uu.id = i.used_by
            ORDER BY i.created_at DESC
            LIMIT 50
        """)).fetchall()

    return _json_response({
        "invitations": [{
            "token": r.token,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "used_at": r.used_at.isoformat() if r.used_at else None,
            "created_by": r.created_by_email,
            "used_by": r.used_by_email,
        } for r in rows]
    })

@router.delete("/api/admin/invitations/{token}")
def api_admin_revoke_invitation(
    token: str, _admin: dict = Depends(require_admin)
) -> Response:
    """Révoque une invitation qui n'a pas encore servi.

    Une invitation déjà consommée est conservée : elle garde la trace de qui a
    créé le compte et quand.
    """
    with SessionLocal() as session:
        row = session.execute(
            text("DELETE FROM invitations WHERE token = :token AND used_at IS NULL "
                 "RETURNING token"),
            {"token": token},
        ).fetchone()
        session.commit()

    if row is None:
        return _json_response(
            {"error": "Invitation introuvable, ou déjà utilisée."}, status_code=404)
    return _json_response({"ok": True})
