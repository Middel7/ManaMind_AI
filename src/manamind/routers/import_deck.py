"""
Router d'import de decklists.
Endpoints : /api/import/parse, /api/import/resolve, /api/import/confirm, /api/import/from-url
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from ..auth import COOKIE_NAME, get_current_user
from ..deck_import.detector import detect
from ..deck_import.models import CanonicalDeckImport, Zone
from ..deck_import.parsers.registry import parse as parse_deck
from ..deck_import.resolver import resolve
from ..deck_import.url_adapter import import_from_url
from ._shared import _json_response

log = logging.getLogger(__name__)
router = APIRouter()

# ── Limite ────────────────────────────────────────────────────────────────────
_MAX_PASTE_BYTES = 2 * 1024 * 1024  # 2 Mo pour le texte collé
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 Mo pour les fichiers


# ── Helpers ──────────────────────────────────────────────────────────────────

def _deck_to_response(deck: CanonicalDeckImport, resolved: bool = False) -> dict:
    d = deck.to_dict()
    d["resolved"] = resolved
    return d


def _require_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    return get_current_user(mm_token=token)


# ── POST /api/import/parse ────────────────────────────────────────────────────

@router.post("/api/import/parse")
async def api_import_parse(request: Request) -> JSONResponse:
    """
    Étape 1 : parse le contenu brut (JSON body avec 'text').
    Détecte le format, parse, retourne le modèle canonique sans résolution DB.
    """
    try:
        _require_user(request)
    except Exception:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    text: str = body.get("text", "")
    if not isinstance(text, str):
        return JSONResponse({"error": "'text' must be a string"}, status_code=400)

    if not text.strip():
        return JSONResponse({"error": "Empty content"}, status_code=400)

    if len(text.encode("utf-8")) > _MAX_PASTE_BYTES:
        return JSONResponse({"error": f"Content too large (max {_MAX_PASTE_BYTES // 1024} KB)"}, status_code=413)

    deck = parse_deck(text)
    detection = detect(text)

    return _json_response({
        "detection": {
            "source": detection.source.value,
            "source_format": detection.source_format,
            "confidence": detection.confidence,
            "alternatives": detection.alternatives,
            "reasons": detection.reasons,
        },
        "deck": _deck_to_response(deck),
    })


# ── POST /api/import/upload ───────────────────────────────────────────────────

@router.post("/api/import/upload")
async def api_import_upload(request: Request, file: UploadFile) -> JSONResponse:
    """
    Étape 1 (variante) : upload d'un fichier.
    Accepte .txt, .csv, .tsv, .dek, .cod, .dck, .xml, .json
    """
    try:
        _require_user(request)
    except Exception:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    allowed_extensions = {".txt", ".csv", ".tsv", ".dek", ".cod", ".dck", ".xml", ".json"}
    filename = file.filename or ""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext and ext not in allowed_extensions:
        return JSONResponse(
            {"error": f"File type not allowed: {ext}. Allowed: {', '.join(sorted(allowed_extensions))}"},
            status_code=415,
        )

    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        return JSONResponse({"error": f"File too large (max {_MAX_UPLOAD_BYTES // (1024*1024)} MB)"}, status_code=413)

    if not content.strip():
        return JSONResponse({"error": "Empty file"}, status_code=400)

    detection = detect(content)
    deck = parse_deck(content)

    return _json_response({
        "filename": filename,
        "detection": {
            "source": detection.source.value,
            "source_format": detection.source_format,
            "confidence": detection.confidence,
            "alternatives": detection.alternatives,
            "reasons": detection.reasons,
        },
        "deck": _deck_to_response(deck),
    })


# ── POST /api/import/resolve ──────────────────────────────────────────────────

@router.post("/api/import/resolve")
async def api_import_resolve(request: Request) -> JSONResponse:
    """
    Étape 2 : résolution des cartes contre la DB Scryfall locale.
    Reçoit le modèle canonique sérialisé, retourne les cartes résolues.
    """
    try:
        _require_user(request)
    except Exception:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    deck_dict: dict = body.get("deck")
    if not deck_dict or not isinstance(deck_dict, dict):
        return JSONResponse({"error": "Missing 'deck' payload"}, status_code=400)

    # Reconstruction légère depuis le dict (sans deserializer complet)
    try:
        deck = _deck_from_dict(deck_dict)
    except Exception as exc:
        return JSONResponse({"error": f"Invalid deck payload: {exc}"}, status_code=400)

    deck = resolve(deck)

    return _json_response({"deck": _deck_to_response(deck, resolved=True)})


# ── POST /api/import/confirm ──────────────────────────────────────────────────

@router.post("/api/import/confirm")
async def api_import_confirm(request: Request) -> JSONResponse:
    """
    Étape 3 : sauvegarde définitive.
    Paramètres :
    - deck : modèle canonique résolu
    - destination : "deck" | "collection"
    - deck_name : nom du deck (si destination="deck")
    - zones : liste des zones à importer (default: commander, mainboard)
    """
    try:
        user = _require_user(request)
    except Exception:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    deck_dict = body.get("deck")
    destination = body.get("destination", "deck")
    deck_name = body.get("deck_name", "")
    included_zones: list[str] = body.get("zones", ["commander", "mainboard"])

    if destination not in ("deck", "collection"):
        return JSONResponse({"error": "destination must be 'deck' or 'collection'"}, status_code=400)

    try:
        deck = _deck_from_dict(deck_dict)
    except Exception as exc:
        return JSONResponse({"error": f"Invalid deck payload: {exc}"}, status_code=400)

    # Filtrer les entrées selon les zones sélectionnées
    filtered = [e for e in deck.entries if e.zone.value in included_zones]

    if not filtered:
        return JSONResponse({"error": "No entries to import after zone filtering"}, status_code=400)

    user_id = user["id"]
    imported = 0
    skipped = 0
    errors: list[str] = []

    if destination == "collection":
        imported, skipped, errors = _save_to_collection(user_id, filtered)
    else:
        commander = next(
            (e.canonical_name or e.raw_name for e in filtered if e.zone == Zone.COMMANDER),
            None,
        )
        name = deck_name or deck.deck_name or commander or "Deck importé"
        imported, skipped, errors = _save_to_deck(user_id, commander or "Unknown", name, filtered)

    return _json_response({
        "ok": True,
        "destination": destination,
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
    })


# ── POST /api/import/from-url ─────────────────────────────────────────────────

@router.post("/api/import/from-url")
async def api_import_from_url(request: Request) -> JSONResponse:
    """
    Import depuis une URL publique.
    Retourne soit le modèle canonique (si OK), soit une erreur + fallback_message.
    """
    try:
        _require_user(request)
    except Exception:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    url: str = body.get("url", "").strip()
    if not url:
        return JSONResponse({"error": "Missing 'url'"}, status_code=400)

    result = import_from_url(url)

    if not result.ok:
        payload: dict[str, Any] = {"ok": False, "error": result.error}
        if result.fallback_message:
            payload["fallback_message"] = result.fallback_message
        return _json_response(payload, status_code=422)

    return _json_response({
        "ok": True,
        "deck": _deck_to_response(result.deck),
    })


# ── Helpers de persistence ────────────────────────────────────────────────────

def _save_to_collection(user_id: int, entries) -> tuple[int, int, list[str]]:
    """Sauvegarde les entrées dans user_collection."""
    from sqlalchemy import text

    from ..db.engine import SessionLocal

    if SessionLocal is None:
        return 0, 0, ["Database unavailable"]

    imported = 0
    skipped = 0
    errors: list[str] = []

    sess = SessionLocal()
    try:
        for entry in entries:
            name = entry.canonical_name or entry.raw_name
            if not name:
                skipped += 1
                continue
            try:
                sess.execute(text("""
                    INSERT INTO user_collection (card_name, quantity, raw_line, user_id)
                    VALUES (:name, :qty, :raw, :uid)
                    ON CONFLICT DO NOTHING
                """), {
                    "name": name,
                    "qty": entry.quantity,
                    "raw": entry.raw_line,
                    "uid": user_id,
                })
                imported += 1
            except Exception as exc:
                errors.append(f"Error saving {name!r}: {exc}")
                skipped += 1
        sess.commit()
    finally:
        sess.close()

    return imported, skipped, errors


def _save_to_deck(user_id: int, commander: str, deck_name: str, entries) -> tuple[int, int, list[str]]:
    """Sauvegarde les entrées dans user_moxfield_decks + user_deck_cards."""
    from ..deck_import.models import Zone
    from ..user_decks import save_deck_for_user, set_deck_cards

    deck_id = f"import-{uuid.uuid4().hex[:12]}"

    try:
        save_deck_for_user(
            user_id=user_id,
            deck_id=deck_id,
            moxfield_url="",
            commander=commander,
            name=deck_name,
        )
    except Exception as exc:
        return 0, 0, [f"Could not create deck: {exc}"]

    # Sérialisation des cartes dans user_deck_cards
    cards: list[dict] = []
    for entry in entries:
        if entry.zone == Zone.COMMANDER:
            continue  # commander déjà stocké dans user_moxfield_decks.commander
        name = entry.canonical_name or entry.raw_name
        if not name:
            continue
        cards.append({"card_name": name, "quantity": entry.quantity})

    imported = 0
    skipped = 0
    errors: list[str] = []

    try:
        set_deck_cards(user_id=user_id, commander=commander, cards=cards)
        imported = len(cards)
    except Exception as exc:
        errors.append(f"Error saving cards: {exc}")
        skipped = len(cards)

    return imported, skipped, errors


# ── Désérialisation légère du modèle canonique ────────────────────────────────

def _deck_from_dict(d: dict) -> CanonicalDeckImport:
    """Reconstruit un CanonicalDeckImport depuis un dict sérialisé."""
    from ..deck_import.models import (
        CanonicalEntry,
        ImportSource,
        ImportStatistics,
        ResolutionStatus,
        Zone,
    )

    deck = CanonicalDeckImport(
        source=ImportSource(d.get("source", "unknown")),
        source_format=d.get("source_format", "unknown"),
        deck_name=d.get("deck_name"),
        format=d.get("format"),
        warnings=d.get("warnings", []),
        errors=d.get("errors", []),
        detected_zones=d.get("detected_zones", []),
    )

    stats_d = d.get("statistics", {})
    deck.statistics = ImportStatistics(
        lines_received=stats_d.get("lines_received", 0),
        cards_detected=stats_d.get("cards_detected", 0),
        copies_detected=stats_d.get("copies_detected", 0),
        exact_matches=stats_d.get("exact_matches", 0),
        ambiguous_matches=stats_d.get("ambiguous_matches", 0),
        unresolved_entries=stats_d.get("unresolved_entries", 0),
        ignored_entries=stats_d.get("ignored_entries", 0),
    )

    for e in d.get("entries", []):
        entry = CanonicalEntry(
            line_number=e.get("line_number", 0),
            raw_line=e.get("raw_line", ""),
            quantity=e.get("quantity", 1),
            raw_name=e.get("raw_name", ""),
            canonical_name=e.get("canonical_name", ""),
            set_code=e.get("set_code"),
            collector_number=e.get("collector_number"),
            language=e.get("language"),
            condition=e.get("condition"),
            finish=e.get("finish"),
            zone=Zone(e.get("zone", "mainboard")),
            tags=e.get("tags", []),
            source_identifier=e.get("source_identifier"),
            scryfall_id=e.get("scryfall_id"),
            oracle_id=e.get("oracle_id"),
            cardmarket_product_id=e.get("cardmarket_product_id"),
            resolution_status=ResolutionStatus(e.get("resolution_status", "unresolved")),
            confidence=e.get("confidence", 0),
            warnings=e.get("warnings", []),
        )
        deck.entries.append(entry)

    return deck
