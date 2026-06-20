#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import re
import unicodedata

from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
UPLOADS_DIR = ROOT / "uploads"
OUTPUTS_DIR = ROOT / "outputs"
OUTPUTS_RECO_DIR = ROOT / "outputs" / "recommendations"
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)
OUTPUTS_RECO_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT / "src"))
from manamind.recommandation_populaire import (  # noqa: E402
    DECKLISTS_ROOT as _POP_DECKLISTS_ROOT,
    load_deck_dataset,
    build_statistics,
    recommend_removals,
    normalize_name as _pop_normalize,
    BASIC_LANDS,
)


def _compute_removals(deck_content: str, commander_name: str, limit: int = 20) -> list[tuple[str, int, float]]:
    """
    Calcule les cartes à retirer via la logique recommandation_populaire.
    Utilisée par l'algorithme Analyse Populaire (V1).
    """
    import re as _re
    # Parser les cartes du deck depuis le contenu texte
    input_cards: set[str] = set()
    for line in deck_content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        m = _re.match(r"^\d+[xX]?\s+(.+)$", line)
        card_name = m.group(1).strip() if m else line
        input_cards.add(_pop_normalize(card_name))

    cmd_norm = _pop_normalize(commander_name)
    input_cards.discard(cmd_norm)
    input_cards -= BASIC_LANDS

    if not input_cards:
        return []

    try:
        decks = load_deck_dataset(_POP_DECKLISTS_ROOT)
        if not decks:
            return []
        deck_frequency, commander_decks, cooccurrence = build_statistics(decks)

        removals_norm = recommend_removals(
            input_cards=input_cards,
            deck_frequency=deck_frequency,
            commander=cmd_norm,
            commander_decks=commander_decks,
            cooccurrence=cooccurrence,
            commander_card=cmd_norm,
            limit=limit + 5,
        )

        # Nombre de decklists connues pour ce commandant (pour calculer le taux réel)
        nb_cmd_decks = len(commander_decks.get(cmd_norm, []))

        # Construire un reverse mapping normalisé → nom original depuis les decklists
        norm_to_original: dict[str, str] = {}
        import csv as _csv2
        cmd_dir = _POP_DECKLISTS_ROOT / _normalize_filename(commander_name)
        if not cmd_dir.exists():
            for sub in _POP_DECKLISTS_ROOT.iterdir():
                if sub.is_dir() and _normalize_filename(sub.name) == _normalize_filename(commander_name):
                    cmd_dir = sub
                    break
        if cmd_dir.exists():
            for csv_file in list(cmd_dir.glob("*.csv"))[:200]:
                try:
                    with open(csv_file, encoding="utf-8-sig", errors="replace") as f:
                        reader = _csv2.DictReader(f, delimiter=";")
                        for row in reader:
                            raw = (row.get("Card Name") or "").strip()
                            if raw:
                                norm_to_original[_pop_normalize(raw)] = raw
                except Exception:
                    continue

        cmd_lower = commander_name.lower()

        def restore(norm: str) -> str:
            return norm_to_original.get(norm, norm.title())

        result = [
            # support = nb decklists de CE commandant contenant la carte
            # taux = support / nb_cmd_decks → entre 0 et 1
            (restore(name), support, round(support / nb_cmd_decks, 4) if nb_cmd_decks > 0 else 0.0)
            for name, support, _raw_freq in removals_norm
            if restore(name).lower() != cmd_lower and name.lower() != cmd_lower
        ][:limit]

        return result
    except Exception as exc:
        print(f"[Retraits] Erreur : {exc}")
        return []


def _normalize_filename(name: str) -> str:
    """Convertit un nom de commandant en slug snake_case ASCII (cohérent avec le script V2)."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-zA-Z0-9\s]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name.lower()


def _extract_commander_from_deck(content: str) -> str | None:
    """Extrait le nom du commandant depuis le contenu texte d'une decklist."""
    lines = content.splitlines()
    sections: list[list[str]] = []
    cur: list[str] = []
    for line in lines:
        t = line.strip()
        if not t:
            if cur:
                sections.append(cur)
                cur = []
        else:
            cur.append(t)
    if cur:
        sections.append(cur)
    # Le commandant est la dernière section à une seule ligne : "1 Nom Du Commandant"
    if len(sections) >= 2:
        last = sections[-1]
        if len(last) == 1:
            m = re.match(r"^\d+\s+(.+)$", last[0])
            if m:
                return m.group(1).strip()
    return None

# ── Base de données (optionnelle : si .env absent, les routes DB retournent 503) ──
sys.path.insert(0, str(ROOT))
try:
    from sqlalchemy import func, select
    from sqlalchemy.orm import aliased

    from src.manamind.db.engine import SessionLocal
    from src.manamind.db.models.card import Card
    from src.manamind.db.models.card_price import CardPrice
    from src.manamind.db.models.card_printing import CardPrinting

    _DB_AVAILABLE = SessionLocal is not None
except Exception:
    SessionLocal = None  # type: ignore[assignment]
    Card = None          # type: ignore[assignment]
    CardPrinting = None  # type: ignore[assignment]
    _DB_AVAILABLE = False

app = FastAPI()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        ROOT / "recommendations_view_slide16.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/cards")
def cards_page() -> FileResponse:
    return FileResponse(
        ROOT / "cards.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/results")
def results_page() -> FileResponse:
    return FileResponse(
        ROOT / "results.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.post("/upload-deck")
async def upload_deck(
    deckfile: UploadFile = File(...),
    algo: str = Form(default="v1"),
) -> JSONResponse:
    filename = Path(deckfile.filename).name
    deck_path = UPLOADS_DIR / filename
    deck_path.write_bytes(await deckfile.read())

    stem = Path(filename).stem

    output_path = OUTPUTS_DIR / f"recommendations_{stem}.csv"
    script = "src/manamind/recommandation_populaire.py"
    output_key = f"/outputs/recommendations_{stem}.csv"

    import os as _os
    _env = _os.environ.copy()
    _env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, script, "--input", str(deck_path), "--output", str(output_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        env=_env,
    )

    if result.returncode != 0:
        return JSONResponse({"error": result.stderr or "Erreur lors de la génération."}, status_code=500)

    return JSONResponse({
        "deckFile": f"/uploads/{filename}",
        "recommendationsFile": output_key,
    })




app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")


# ── API recherche de cartes ───────────────────────────────────────────────────
@app.get("/api/cards/search")
def search_cards(
    q: str = Query(default="", description="Texte à rechercher dans le nom des cartes"),
    limit: int = Query(default=100, ge=1, le=100),
) -> JSONResponse:
    """
    Recherche de cartes par nom (contains, case-insensitive).
    Retourne au maximum 100 résultats triés par nom.
    Requiert que la base PostgreSQL soit configurée (.env) et que l'import ait été lancé.
    """
    if not _DB_AVAILABLE:
        return JSONResponse(
            {
                "error": (
                    "Base de données non configurée. "
                    "Crée un fichier .env avec DATABASE_URL puis lance "
                    "python scripts/import_scryfall_cards.py"
                )
            },
            status_code=503,
        )

    q = q.strip()
    if len(q) < 2:
        return JSONResponse({"cards": [], "total": 0, "query": q})

    try:
        with SessionLocal() as session:
            # Sous-requête 1a : rang de chaque impression par prix EUR décroissant
            # row_number() = 1 → impression la plus chère de la carte
            expensive_rank_subq = (
                select(
                    CardPrinting.card_id,
                    CardPrinting.id.label("pid"),
                    CardPrinting.image_normal,
                    CardPrinting.scryfall_uri,
                    func.row_number().over(
                        partition_by=CardPrinting.card_id,
                        order_by=CardPrice.price.desc().nulls_last(),
                    ).label("rn"),
                )
                .join(CardPrice, CardPrinting.id == CardPrice.printing_id)
                .where(
                    CardPrice.currency == "eur",
                    CardPrice.price_type == "regular",
                    CardPrice.price > 0,
                )
                .subquery()
            )
            expensive_printing_subq = (
                select(
                    expensive_rank_subq.c.card_id,
                    expensive_rank_subq.c.image_normal,
                    expensive_rank_subq.c.scryfall_uri,
                )
                .where(expensive_rank_subq.c.rn == 1)
                .subquery()
            )

            # Sous-requête 1b : première impression (fallback si aucun prix disponible)
            first_printing_subq = (
                select(
                    CardPrinting.card_id,
                    func.min(CardPrinting.id).label("pid"),
                )
                .group_by(CardPrinting.card_id)
                .subquery()
            )
            FallbackPrinting = aliased(CardPrinting)

            # Sous-requête 2 : prix EUR minimum (le moins cher) parmi toutes les impressions
            price_subq = (
                select(
                    CardPrinting.card_id,
                    func.min(CardPrice.price).label("eur_price"),
                )
                .join(CardPrice, CardPrinting.id == CardPrice.printing_id)
                .where(
                    CardPrice.currency == "eur",
                    CardPrice.price_type == "regular",
                    CardPrice.price > 0,
                )
                .group_by(CardPrinting.card_id)
                .subquery()
            )

            stmt = (
                select(
                    Card,
                    func.coalesce(
                        expensive_printing_subq.c.image_normal,
                        FallbackPrinting.image_normal,
                    ).label("image_normal"),
                    func.coalesce(
                        expensive_printing_subq.c.scryfall_uri,
                        FallbackPrinting.scryfall_uri,
                    ).label("scryfall_uri"),
                    price_subq.c.eur_price,
                )
                .outerjoin(expensive_printing_subq, Card.id == expensive_printing_subq.c.card_id)
                .outerjoin(first_printing_subq, Card.id == first_printing_subq.c.card_id)
                .outerjoin(FallbackPrinting, FallbackPrinting.id == first_printing_subq.c.pid)
                .outerjoin(price_subq, Card.id == price_subq.c.card_id)
                # ilike = ILIKE PostgreSQL : case-insensitive, paramétré → pas d'injection SQL
                .where(Card.name.ilike(f"%{q}%"))
                # Tri par popularité EDHREC (1 = plus populaire), sans rank en dernier
                .order_by(Card.edhrec_rank.asc().nulls_last(), Card.name)
                .limit(limit)
            )

            rows = session.execute(stmt).all()

            cards = []
            for card, image_normal, scryfall_uri, eur_price in rows:
                oracle_text = card.oracle_text or ""
                if len(oracle_text) > 280:
                    oracle_text = oracle_text[:280] + "…"

                cards.append({
                    "name": card.name,
                    "mana_cost": card.mana_cost,
                    "type_line": card.type_line,
                    "oracle_text": oracle_text,
                    "legal_commander": card.legal_commander,
                    "colors": card.colors or [],
                    "edhrec_rank": card.edhrec_rank,
                    "eur_price": float(eur_price) if eur_price is not None else None,
                    "image_normal": image_normal,
                    "scryfall_uri": scryfall_uri,
                })

            return JSONResponse({"cards": cards, "total": len(cards), "query": q})

    except Exception as exc:
        return JSONResponse(
            {"error": f"Erreur base de données : {exc}"},
            status_code=500,
        )


@app.get("/{filename:path}")
def static_file(filename: str) -> FileResponse:
    file_path = ROOT / filename
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    return JSONResponse({"error": "Not found"}, status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
