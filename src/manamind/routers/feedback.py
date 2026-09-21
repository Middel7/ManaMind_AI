"""Router des retours d'utilisateurs.

Le bouton « Help me improve », présent sur chaque écran, ouvre une fenêtre de
saisie. Ce qui s'y écrit arrive ici, et se relit dans l'écran d'administration.

Deux publics, deux jeux de routes : l'utilisateur ne peut qu'écrire, et ne voit
jamais ce que les autres ont envoyé ; l'administrateur lit, marque et supprime.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import text

from manamind.auth import COOKIE_NAME, get_current_user, require_admin
from manamind.db.engine import SessionLocal

from manamind.routers._shared import _json_response

router = APIRouter()

# Un retour utile tient en quelques phrases. La borne n'est pas là pour
# rationner : elle empêche qu'un copier-coller malheureux ne verse un fichier
# entier dans la table.
LONGUEUR_MAX = 4000


def _user(request: Request) -> dict:
    return get_current_user(mm_token=request.cookies.get(COOKIE_NAME))


@router.post("/api/feedback")
async def api_envoyer_retour(request: Request) -> Response:
    """Enregistre un retour écrit par l'utilisateur connecté."""
    user = _user(request)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Corps JSON invalide"}, status_code=400)

    message = (body.get("message") or "").strip()
    if not message:
        return _json_response({"error": "Écrivez quelque chose avant d'envoyer."},
                              status_code=400)
    if len(message) > LONGUEUR_MAX:
        return _json_response(
            {"error": f"Message trop long : {len(message)} caractères pour "
                      f"{LONGUEUR_MAX} au maximum."},
            status_code=400)

    with SessionLocal() as session:
        session.execute(text("""
            INSERT INTO user_feedback (user_id, message)
            VALUES (:uid, :message)
        """), {"uid": user["id"], "message": message})
        session.commit()

    return _json_response({"ok": True})


@router.get("/api/admin/feedback")
def api_lire_retours(
    _admin: dict = Depends(require_admin),
    limit: int = Query(default=100, ge=1, le=500),
) -> Response:
    """Retours reçus : les non traités d'abord, les plus récents en tête."""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT f.id, f.message, f.created_at, f.handled_at,
                   COALESCE(u.display_name, u.email) AS auteur,
                   u.email
            FROM user_feedback f
            JOIN users u ON u.id = f.user_id
            ORDER BY f.handled_at NULLS FIRST, f.created_at DESC
            LIMIT :limit
        """), {"limit": limit}).fetchall()

    retours = [
        {
            "id": r.id,
            "message": r.message,
            "auteur": r.auteur,
            "email": r.email,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "handled": r.handled_at is not None,
        }
        for r in rows
    ]
    return _json_response({
        "feedback": retours,
        "pending": sum(1 for r in retours if not r["handled"]),
    })


@router.post("/api/admin/feedback/{feedback_id}/handled")
async def api_marquer_retour(
    feedback_id: int,
    request: Request,
    _admin: dict = Depends(require_admin),
) -> Response:
    """Marque un retour comme traité, ou le remet en attente."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    traite = bool(body.get("handled", True))

    with SessionLocal() as session:
        result = session.execute(text("""
            UPDATE user_feedback
               SET handled_at = CASE WHEN :traite THEN now() ELSE NULL END
             WHERE id = :fid
        """), {"fid": feedback_id, "traite": traite})
        session.commit()

    if not result.rowcount:
        raise HTTPException(status_code=404, detail="Retour introuvable")
    return _json_response({"ok": True, "handled": traite})


@router.delete("/api/admin/feedback/{feedback_id}")
def api_supprimer_retour(
    feedback_id: int,
    _admin: dict = Depends(require_admin),
) -> Response:
    """Supprime définitivement un retour."""
    with SessionLocal() as session:
        result = session.execute(
            text("DELETE FROM user_feedback WHERE id = :fid"), {"fid": feedback_id})
        session.commit()

    if not result.rowcount:
        raise HTTPException(status_code=404, detail="Retour introuvable")
    return _json_response({"ok": True})
