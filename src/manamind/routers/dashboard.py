"""Tableau de bord et profil utilisateur.

Le dashboard est la page d'accueil : il montre l'etat de la collection, ce qui
manque pour en tirer parti, et relance sur la mise a jour quand elle date.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from sqlalchemy import text

from manamind import collection_store as store
from manamind.auth import COOKIE_NAME, get_current_user
from manamind.db.engine import SessionLocal

from ._shared import _json_response

router = APIRouter()

# Au-dela, on invite explicitement a verifier la collection.
STALE_AFTER_DAYS = 30


def _user(request: Request) -> dict:
    return get_current_user(mm_token=request.cookies.get(COOKIE_NAME))


def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).days


def _load_profile(user_id: int) -> dict:
    with SessionLocal() as session:
        row = session.execute(text("""
            SELECT avatar_scryfall_id, avatar_card_name, banner_scryfall_id,
                   banner_card_name, favorite_commander, collection_checked_at,
                   onboarding_dismissed
            FROM user_profiles WHERE user_id = :uid
        """), {"uid": user_id}).fetchone()
    if row is None:
        return {
            "avatar_scryfall_id": None, "avatar_card_name": None,
            "banner_scryfall_id": None, "banner_card_name": None,
            "favorite_commander": None, "collection_checked_at": None,
            "onboarding_dismissed": False,
        }
    return {
        "avatar_scryfall_id": row.avatar_scryfall_id,
        "avatar_card_name": row.avatar_card_name,
        "banner_scryfall_id": row.banner_scryfall_id,
        "banner_card_name": row.banner_card_name,
        "favorite_commander": row.favorite_commander,
        "collection_checked_at": row.collection_checked_at.isoformat()
        if row.collection_checked_at else None,
        "onboarding_dismissed": bool(row.onboarding_dismissed),
    }


def _progress(user: dict, profile: dict, stats: dict) -> dict:
    """Trois jalons, chacun debloquant une capacite reelle de l'outil."""
    has_identity = bool(
        (user.get("display_name") or "").strip() and profile.get("avatar_scryfall_id")
    )
    steps = [
        {
            "key": "collection",
            "label": "Importer ma collection",
            "detail": "Débloque toutes les analyses croisant vos cartes et vos decks.",
            "done": stats["copies"] > 0,
            "value": f"{stats['copies']} exemplaires" if stats["copies"] else None,
            "href": "/collection/import",
            "cta": "Importer",
        },
        {
            "key": "deck",
            "label": "Ajouter un premier deck",
            "detail": "Permet de savoir quelles cartes vous dorment entre les mains.",
            "done": stats["decks"] > 0,
            "value": f"{stats['decks']} decks" if stats["decks"] else None,
            "href": "/decks",
            "cta": "Ajouter un deck",
        },
        {
            "key": "identity",
            "label": "Compléter mon profil",
            "detail": "Un pseudo et une carte fétiche, pour que l'espace soit le vôtre.",
            "done": has_identity,
            "value": user.get("display_name") if has_identity else None,
            "href": "/profil",
            "cta": "Compléter",
        },
    ]
    done = sum(1 for step in steps if step["done"])
    return {
        "steps": steps,
        "done": done,
        "total": len(steps),
        "percent": round(done / len(steps) * 100),
        "complete": done == len(steps),
    }


@router.get("/api/dashboard")
def api_dashboard(request: Request) -> Response:
    user = _user(request)
    stats = store.stats(user["id"])
    profile = _load_profile(user["id"])

    with SessionLocal() as session:
        decks = session.execute(text("""
            SELECT d.deck_id, d.name, d.commander,
                   COALESCE(d.fetched_at, d.created_at) AS updated_at,
                   COALESCE(cards.n, 0) AS card_count
            FROM user_moxfield_decks d
            LEFT JOIN LATERAL (
                SELECT SUM(dc.quantity) AS n
                FROM user_deck_cards dc
                WHERE dc.user_id = d.user_id
                  AND mm_normalize_name(dc.commander) = mm_normalize_name(d.commander)
            ) cards ON TRUE
            WHERE d.user_id = :uid
            ORDER BY COALESCE(d.fetched_at, d.created_at) DESC NULLS LAST
            LIMIT 6
        """), {"uid": user["id"]}).fetchall()

    days = _days_since(stats["last_update"])
    checked_days = _days_since(profile["collection_checked_at"])
    freshness_days = min(d for d in (days, checked_days) if d is not None) \
        if (days is not None or checked_days is not None) else None

    return _json_response({
        "user": {
            "id": user["id"],
            "email": user.get("email"),
            "display_name": user.get("display_name"),
            "role": user.get("role"),
        },
        "profile": profile,
        "stats": stats,
        "progress": _progress(user, profile, stats),
        "freshness": {
            "days": freshness_days,
            "stale": freshness_days is not None and freshness_days >= STALE_AFTER_DAYS,
            "threshold": STALE_AFTER_DAYS,
        },
        "decks": [
            {
                "deck_id": d.deck_id,
                "name": d.name or d.commander,
                "commander": d.commander,
                "card_count": int(d.card_count or 0),
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in decks
        ],
    })


@router.get("/api/dashboard/highlights")
def api_highlights(request: Request, limit: int = Query(8, ge=1, le=30)) -> Response:
    """Cartes a mettre en avant : dormantes de valeur, puis ajouts recents."""
    user = _user(request)
    return _json_response({
        "dormant": store.dormant_items(user["id"], limit),
        "recent": store.recent_items(user["id"], limit),
    })


# ── Profil ────────────────────────────────────────────────────────────────────

@router.get("/api/profile")
def api_get_profile(request: Request) -> Response:
    user = _user(request)
    return _json_response({
        "user": {
            "id": user["id"],
            "email": user.get("email"),
            "display_name": user.get("display_name"),
            "role": user.get("role"),
        },
        "profile": _load_profile(user["id"]),
        "stats": store.stats(user["id"]),
    })


@router.put("/api/profile")
async def api_update_profile(request: Request) -> Response:
    user = _user(request)
    body = await request.json()

    display_name = body.get("display_name")
    fields = {
        key: body.get(key)
        for key in (
            "avatar_scryfall_id", "avatar_card_name",
            "banner_scryfall_id", "banner_card_name",
            "favorite_commander",
        )
        if key in body
    }

    with SessionLocal() as session:
        if isinstance(display_name, str) and display_name.strip():
            session.execute(
                text("UPDATE users SET display_name = :name WHERE id = :uid"),
                {"name": display_name.strip()[:40], "uid": user["id"]},
            )

        session.execute(text("""
            INSERT INTO user_profiles (user_id) VALUES (:uid)
            ON CONFLICT (user_id) DO NOTHING
        """), {"uid": user["id"]})

        if fields:
            assignments = ", ".join(f"{k} = :{k}" for k in fields)
            session.execute(
                text(f"UPDATE user_profiles SET {assignments}, updated_at = NOW() "
                     "WHERE user_id = :uid"),
                {**fields, "uid": user["id"]},
            )
        session.commit()

    return _json_response({"ok": True, "profile": _load_profile(user["id"])})


@router.post("/api/profile/collection-checked")
def api_mark_checked(request: Request) -> Response:
    """L'utilisateur declare sa collection a jour : remet le compteur a zero."""
    user = _user(request)
    with SessionLocal() as session:
        session.execute(text("""
            INSERT INTO user_profiles (user_id, collection_checked_at)
            VALUES (:uid, NOW())
            ON CONFLICT (user_id) DO UPDATE SET collection_checked_at = NOW(),
                                                updated_at = NOW()
        """), {"uid": user["id"]})
        session.commit()
    return _json_response({"ok": True, "checked_at": datetime.now(timezone.utc).isoformat()})
