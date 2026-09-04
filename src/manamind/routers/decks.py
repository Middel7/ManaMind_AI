"""Router decks — routes /api/moxfield-decks, /api/deck/*, /upload-deck, /api/opened-sets/*."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from manamind.routers._shared import ROOT, UPLOADS_DIR, OUTPUTS_DIR, _json_response

router = APIRouter()

_ALLOWED_UPLOAD_EXT = {".txt"}
_MAX_UPLOAD_BYTES = 500 * 1024  # 500 Ko


@router.post("/upload-deck")
async def upload_deck(
    deckfile: UploadFile = File(...),
    algo: str = Form(default="v1"),
) -> JSONResponse:
    import uuid as _uuid
    # Validation de l'extension
    ext = Path(deckfile.filename or "").suffix.lower()
    if ext not in _ALLOWED_UPLOAD_EXT:
        return _json_response({"error": "Seuls les fichiers .txt sont acceptés."}, status_code=400)

    # Lecture et validation de la taille
    content = await deckfile.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        return _json_response({"error": "Fichier trop volumineux (max 500 Ko)."}, status_code=400)

    # Sanitisation du nom : caractères autorisés uniquement, préfixé par UUID pour unicité
    raw_stem = Path(deckfile.filename or "deck").stem
    safe_stem = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", raw_stem)
    filename = f"{safe_stem}_{_uuid.uuid4().hex[:8]}.txt"
    deck_path = UPLOADS_DIR / filename
    deck_path.write_bytes(content)

    stem = Path(filename).stem

    output_path = OUTPUTS_DIR / f"recommendations_{stem}.csv"
    script = "src/manamind/recommandation_populaire.py"
    output_key = f"/outputs/recommendations_{stem}.csv"

    import os as _os
    import asyncio as _asyncio
    _env = _os.environ.copy()
    _env["PYTHONIOENCODING"] = "utf-8"
    proc = await _asyncio.create_subprocess_exec(
        sys.executable, script, "--input", str(deck_path), "--output", str(output_path),
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
        cwd=str(ROOT),
        env=_env,
    )
    _stdout, _stderr = await proc.communicate()
    result_returncode = proc.returncode
    result_stderr = _stderr.decode("utf-8", errors="replace")

    if result_returncode != 0:
        return JSONResponse({"error": result_stderr or "Erreur lors de la génération."}, status_code=500)

    return JSONResponse({
        "deckFile": f"/uploads/{filename}",
        "recommendationsFile": output_key,
    })


@router.get("/api/moxfield-decks")
def api_moxfield_list(request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.user_decks import load_config_for_user
    decks = load_config_for_user(user["id"])
    # Sérialiser les datetimes
    for d in decks:
        if d.get("fetched_at") and hasattr(d["fetched_at"], "isoformat"):
            d["fetched_at"] = d["fetched_at"].isoformat()
    return _json_response({"decks": decks})



@router.delete("/api/moxfield-decks/{deck_id}")
def api_moxfield_delete(deck_id: str, request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.user_decks import remove_deck_for_user
    ok = remove_deck_for_user(user["id"], deck_id)
    return _json_response({"ok": ok})


@router.get("/api/deck-detail/{deck_id}")
def api_deck_detail(deck_id: str, request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.user_decks import load_config_for_user, get_deck_cards
    decks = load_config_for_user(user["id"])
    entry = next((d for d in decks if d["deck_id"] == deck_id), None)
    if not entry:
        return _json_response({"error": "Deck introuvable"}, status_code=404)
    cards_raw = get_deck_cards(user["id"], entry["commander"])
    cards = [{"card_name": name, "quantity": qty} for name, qty in sorted(cards_raw, key=lambda x: x[0])]
    result = dict(entry)
    if result.get("fetched_at") and hasattr(result["fetched_at"], "isoformat"):
        result["fetched_at"] = result["fetched_at"].isoformat()
    result["cards"] = cards
    return _json_response(result)


@router.post("/api/deck-rename/{deck_id}")
async def api_deck_rename(deck_id: str, request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Corps JSON invalide"}, status_code=400)
    new_name = (body.get("name") or "").strip()
    if not new_name:
        return _json_response({"error": "Nom vide"}, status_code=400)
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    with SessionLocal() as sess:
        result = sess.execute(_t("""
            UPDATE user_moxfield_decks SET name = :name
            WHERE user_id = :uid AND deck_id = :did
        """), {"name": new_name, "uid": user["id"], "did": deck_id})
        sess.commit()
    if result.rowcount == 0:
        return _json_response({"error": "Deck introuvable"}, status_code=404)
    return _json_response({"ok": True})


@router.get("/api/deck-txt/{deck_id}")
def api_deck_txt(deck_id: str, request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    from manamind.user_decks import load_config_for_user, get_deck_txt_content
    decks = load_config_for_user(user["id"])
    entry = next((d for d in decks if d["deck_id"] == deck_id), None)
    if not entry:
        return _json_response({"error": "Deck introuvable"}, status_code=404)
    content = get_deck_txt_content(user["id"], entry["commander"])
    return _json_response({"ok": True, "content": content or "", "commander": entry["commander"]})


@router.post("/api/deck-card/add")
async def api_deck_card_add(request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    body = await request.json()
    commander = (body.get("commander") or "").strip()
    deck_id   = (body.get("deck_id") or "").strip() or None
    card      = (body.get("card_name") or "").strip()
    if not (commander or deck_id) or not card:
        return _json_response({"error": "Paramètres manquants"}, status_code=400)
    try:
        from manamind.user_decks import add_card_to_deck_db
        add_card_to_deck_db(user["id"], commander, card, deck_id)
        return _json_response({"ok": True})
    except Exception as e:
        return _json_response({"error": str(e)}, status_code=500)


@router.post("/api/deck-card/remove")
async def api_deck_card_remove(request: Request) -> JSONResponse:
    from manamind.auth import get_current_user, COOKIE_NAME
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    body = await request.json()
    commander = (body.get("commander") or "").strip()
    deck_id   = (body.get("deck_id") or "").strip() or None
    card      = (body.get("card_name") or "").strip()
    if not (commander or deck_id) or not card:
        return _json_response({"error": "Paramètres manquants"}, status_code=400)
    try:
        from manamind.user_decks import remove_card_from_deck_db
        found = remove_card_from_deck_db(user["id"], commander, card, deck_id,
                                         all_copies=bool(body.get("all")))
        if not found:
            return _json_response({"ok": False, "error": f"« {card} » n'est pas dans le deck de {commander}"}, status_code=404)
        return _json_response({"ok": True})
    except Exception as e:
        return _json_response({"error": str(e)}, status_code=500)



@router.get("/api/opened-sets")
def api_opened_sets(request: Request) -> JSONResponse:
    """Retourne la liste des codes de sets ouverts depuis user_opened_sets (DB)."""
    from manamind.auth import get_current_user, COOKIE_NAME
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    with SessionLocal() as sess:
        rows = sess.execute(_t(
            "SELECT set_code FROM user_opened_sets WHERE user_id = :uid ORDER BY set_code"
        ), {"uid": user["id"]}).fetchall()
    codes = [r.set_code.upper() for r in rows]
    return _json_response({"sets": codes})


@router.post("/api/opened-sets")
async def api_opened_sets_post(request: Request) -> JSONResponse:
    """Ajoute ou supprime des codes de sets dans user_opened_sets (DB).

    Body JSON : { "action": "add"|"remove", "set_code": "ABC" }
    """
    from manamind.auth import get_current_user, COOKIE_NAME
    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    user = get_current_user(mm_token=request.cookies.get(COOKIE_NAME))
    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Corps JSON invalide"}, status_code=400)
    action = body.get("action", "").strip().lower()
    set_code = (body.get("set_code") or "").strip().upper()
    if not set_code:
        return _json_response({"error": "set_code manquant"}, status_code=400)
    with SessionLocal() as sess:
        if action == "add":
            sess.execute(_t("""
                INSERT INTO user_opened_sets (user_id, set_code)
                VALUES (:uid, :code)
                ON CONFLICT DO NOTHING
            """), {"uid": user["id"], "code": set_code})
            sess.commit()
            return _json_response({"ok": True, "action": "add", "set_code": set_code})
        elif action == "remove":
            sess.execute(_t("""
                DELETE FROM user_opened_sets WHERE user_id = :uid AND set_code = :code
            """), {"uid": user["id"], "code": set_code})
            sess.commit()
            return _json_response({"ok": True, "action": "remove", "set_code": set_code})
        else:
            return _json_response({"error": "action doit être 'add' ou 'remove'"}, status_code=400)


@router.post("/api/deck-freq")
async def api_deck_freq(request: Request) -> JSONResponse:
    """
    Retourne le taux d'inclusion (%) par carte pour un commandant donné.
    Body: { "commander": "Kyler, Sigardian Emissary", "cards": ["Swords to Plowshares", ...] }
    Réponse: { "rates": { "Swords to Plowshares": 34.2, ... } }
    """
    body = await request.json()
    commander = (body.get("commander") or "").strip()
    cards: list[str] = body.get("cards") or []
    if not commander or not cards:
        return _json_response({"rates": {}})

    from sqlalchemy import text as _t
    from manamind.db.engine import SessionLocal
    with SessionLocal() as sess:
        rows = sess.execute(_t("""
            SELECT card_name,
                   ROUND(inclusion_rate::numeric, 1) AS pct
            FROM deck_stat_commander
            WHERE LOWER(TRIM(commander)) = LOWER(TRIM(:cmd))
              AND card_name = ANY(:cards)
        """), {"cmd": commander, "cards": cards}).fetchall()

    rates = {r.card_name: float(r.pct) for r in rows}
    return _json_response({"rates": rates})
