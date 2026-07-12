"""
auth.py — Authentification ManaMind
JWT dans cookie httpOnly + hash bcrypt
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt as _bcrypt
from fastapi import Cookie, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import text

from manamind.db.engine import SessionLocal

_DEFAULT_SECRET = "changeme-set-JWT_SECRET-in-env"
SECRET_KEY: str = os.environ.get("JWT_SECRET", _DEFAULT_SECRET)
if SECRET_KEY == _DEFAULT_SECRET:
    import warnings
    warnings.warn(
        "JWT_SECRET non défini — utilisation d'une clé par défaut INSECURE. "
        "Définir la variable d'environnement JWT_SECRET en production.",
        stacklevel=1,
    )
ALGORITHM  = os.environ.get("JWT_ALGORITHM", "HS256")
EXPIRE_DAYS = int(os.environ.get("JWT_EXPIRE_DAYS", "7"))

COOKIE_NAME = "mm_token"


# ── Mot de passe ──────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=EXPIRE_DAYS)
    return jwt.encode({"sub": str(user_id), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> int:
    """Retourne user_id ou lève HTTPException 401."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            raise _unauth()
        return int(sub)
    except JWTError:
        raise _unauth()


def _unauth() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Non authentifié",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ── Dépendance FastAPI ────────────────────────────────────────────────────────

def get_current_user(mm_token: Optional[str] = Cookie(default=None)) -> dict:
    """
    Dépendance FastAPI : lit le cookie mm_token, retourne le row user.
    Lève 401 si absent ou invalide, 403 si compte désactivé.
    """
    if not mm_token:
        raise _unauth()
    user_id = decode_token(mm_token)
    with SessionLocal() as sess:
        row = sess.execute(
            text("SELECT id, email, display_name, role, is_active FROM users WHERE id = :id"),
            {"id": user_id},
        ).fetchone()
    if row is None:
        raise _unauth()
    if not row.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Compte désactivé")
    return {"id": row.id, "email": row.email, "display_name": row.display_name, "role": row.role}


def require_admin(mm_token: Optional[str] = Cookie(default=None)) -> dict:
    """Dépendance FastAPI : idem get_current_user mais exige le rôle admin."""
    user = get_current_user(mm_token)
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès réservé à l'admin")
    return user


# ── Helpers DB ────────────────────────────────────────────────────────────────

def get_user_by_email(email: str) -> Optional[dict]:
    with SessionLocal() as sess:
        row = sess.execute(
            text("SELECT id, email, password_hash, role, is_active, display_name FROM users WHERE LOWER(email) = LOWER(:email)"),
            {"email": email},
        ).fetchone()
    if row is None:
        return None
    return dict(row._mapping)


def create_user(email: str, password: str, role: str = "user", display_name: str | None = None) -> int:
    """Crée un user, retourne son id."""
    pw_hash = hash_password(password)
    with SessionLocal() as sess:
        row = sess.execute(
            text("""
                INSERT INTO users (email, password_hash, display_name, role)
                VALUES (:email, :pw, :name, :role)
                RETURNING id
            """),
            {"email": email, "pw": pw_hash, "name": display_name or email.split("@")[0], "role": role},
        ).fetchone()
        sess.commit()
    return row[0]


def validate_invitation(token: str) -> int:
    """Valide un token d'invitation. Retourne l'id de l'invitation ou lève HTTPException."""
    with SessionLocal() as sess:
        row = sess.execute(
            text("""
                SELECT id, expires_at, used_at
                FROM invitations
                WHERE token = :token
            """),
            {"token": token},
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="Lien d'invitation invalide")
    if row.used_at is not None:
        raise HTTPException(status_code=400, detail="Ce lien d'invitation a déjà été utilisé")
    if row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Ce lien d'invitation a expiré")
    return row.id


def consume_invitation(invitation_id: int, user_id: int) -> None:
    """Marque l'invitation comme utilisée."""
    with SessionLocal() as sess:
        sess.execute(
            text("UPDATE invitations SET used_by = :uid, used_at = NOW() WHERE id = :iid"),
            {"uid": user_id, "iid": invitation_id},
        )
        sess.commit()


def register_with_invitation(token: str, email: str, password: str, display_name: str | None = None) -> tuple[int, str]:
    """
    Crée un utilisateur et consomme l'invitation dans une seule transaction atomique.
    Retourne (user_id, display_name).
    Lève HTTPException si l'invitation est invalide/déjà utilisée, ou l'email déjà pris.
    """
    pw_hash = hash_password(password)
    dn = display_name or email.split("@")[0]

    with SessionLocal() as sess:
        # 1. Tenter de consommer l'invitation atomiquement
        #    UPDATE ... WHERE used_at IS NULL → 0 lignes = déjà utilisée
        row = sess.execute(
            text("""
                UPDATE invitations
                SET used_at = NOW()
                WHERE token = :token
                  AND used_at IS NULL
                  AND (expires_at IS NULL OR expires_at > NOW())
                RETURNING id
            """),
            {"token": token},
        ).fetchone()

        if row is None:
            # Vérifier si elle existe mais est utilisée/expirée pour un meilleur message
            inv = sess.execute(
                text("SELECT used_at, expires_at FROM invitations WHERE token = :token"),
                {"token": token},
            ).fetchone()
            if inv is None:
                raise HTTPException(status_code=400, detail="Lien d'invitation invalide")
            if inv.used_at is not None:
                raise HTTPException(status_code=400, detail="Ce lien d'invitation a déjà été utilisé")
            raise HTTPException(status_code=400, detail="Ce lien d'invitation a expiré")

        inv_id = row.id

        # 2. Vérifier unicité email dans la même transaction
        existing = sess.execute(
            text("SELECT id FROM users WHERE LOWER(email) = LOWER(:email)"),
            {"email": email},
        ).fetchone()
        if existing is not None:
            # Rollback implicite via le context manager — invitation non consommée
            sess.rollback()
            raise HTTPException(status_code=400, detail="Cet email est déjà utilisé")

        # 3. Créer l'utilisateur
        user_row = sess.execute(
            text("""
                INSERT INTO users (email, password_hash, display_name, role)
                VALUES (:email, :pw, :name, 'user')
                RETURNING id
            """),
            {"email": email, "pw": pw_hash, "name": dn},
        ).fetchone()
        user_id = user_row[0]

        # 4. Lier l'invitation à l'utilisateur
        sess.execute(
            text("UPDATE invitations SET used_by = :uid WHERE id = :iid"),
            {"uid": user_id, "iid": inv_id},
        )

        sess.commit()

    return user_id, dn
