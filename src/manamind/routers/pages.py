"""Router pour les pages HTML et routes admin.

L'arborescence est organisee autour des deux objets que manipule l'utilisateur
— sa collection et ses decks — plutot que par outil. Les anciennes URL, nommees
d'apres les outils, redirigent en permanent vers les nouvelles.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse

from manamind.routers._shared import ROOT

router = APIRouter()

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


def _page(filename: str) -> FileResponse:
    return FileResponse(ROOT / filename, media_type="text/html", headers=_NO_CACHE)


# ── Collection ────────────────────────────────────────────────────────────────

@router.get("/collection")
def page_collection() -> FileResponse:
    return _page("collection.html")


@router.get("/collection/import")
def page_collection_import() -> FileResponse:
    return _page("collection_import.html")


@router.get("/collection/ajout")
def page_collection_add() -> FileResponse:
    return _page("collection_add.html")


@router.get("/collection/boosters")
def page_collection_boosters() -> FileResponse:
    return _page("collection_boosters.html")


@router.get("/collection/commandants")
def page_collection_commanders() -> FileResponse:
    return _page("collection_commanders.html")


@router.get("/collection/commandants/{commander}")
def page_collection_commander_build(commander: str) -> FileResponse:
    return _page("collection_commander_build.html")


# ── Decks ─────────────────────────────────────────────────────────────────────
# Les routes litterales sont declarees avant /decks/{deck_id}, sinon elles
# seraient capturees comme des identifiants.

@router.get("/decks")
def page_decks() -> FileResponse:
    return _page("decks.html")


@router.get("/decks/nouveau")
def page_deck_new() -> FileResponse:
    return _page("deck_new.html")


@router.get("/decks/ameliorer")
def page_deck_improve() -> FileResponse:
    return _page("deck_improve.html")


@router.get("/decks/alleger")
def page_deck_trim() -> FileResponse:
    return _page("deck_trim.html")


@router.get("/decks/commandant")
def page_deck_swap() -> FileResponse:
    return _page("deck_swap.html")


@router.get("/decks/commandant/detail")
def page_deck_swap_detail() -> FileResponse:
    return _page("deck_swap_detail.html")


@router.get("/decks/deplacements")
def page_deck_moves() -> FileResponse:
    return _page("deck_moves.html")


@router.get("/decks/analyse")
def page_deck_analyze() -> FileResponse:
    return _page("deck_analyze.html")


@router.get("/decks/{deck_id}")
def page_deck_detail(deck_id: str) -> FileResponse:
    return _page("deck_detail.html")


# ── Cartes, analyse, profil ───────────────────────────────────────────────────

@router.get("/cartes/commandant")
def page_card_commander() -> FileResponse:
    return _page("card_commander.html")


@router.get("/analyse")
def page_analysis() -> FileResponse:
    return _page("results.html")


@router.get("/profil")
def page_profile() -> FileResponse:
    return _page("profile.html")


@router.get("/admin")
def admin_page() -> FileResponse:
    return _page("admin.html")


# ── Installation sur l'écran d'accueil ────────────────────────────────────────
# Les deux fichiers vivent dans `static/` avec le reste du front, mais se
# servent depuis la racine : un service worker ne contrôle que les URL situées
# sous la sienne, et `/static/js/sw.js` ne verrait jamais passer une navigation
# vers `/decks`. Le manifeste les rejoint par symétrie, avec son type MIME
# propre — `mimetypes` ne connaît pas `.webmanifest` sur toutes les machines.

@router.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest() -> FileResponse:
    return FileResponse(
        ROOT / "static" / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(
        ROOT / "static" / "js" / "sw.js",
        media_type="text/javascript",
        headers={
            "Cache-Control": "no-cache",
            # Redondant tant que le fichier est servi depuis la racine, mais
            # c'est ce qui autorisera un jour un autre chemin sans casse.
            "Service-Worker-Allowed": "/",
        },
    )


# ── Redirections depuis l'ancienne arborescence ───────────────────────────────

_LEGACY_REDIRECTS = {
    "/results": "/analyse",
    "/collection-manage": "/collection",
    "/collection-commanders": "/collection/commandants",
    "/deck-config": "/collection/import",
    "/deck-edit": "/decks",
    "/deck-build": "/decks/ameliorer",
    "/deck-trim": "/decks/alleger",
    "/deck-moves": "/decks/deplacements",
    "/deck-select": "/decks/analyse",
    "/commander-suggest": "/cartes/commandant",
    "/commander-swap": "/decks/commandant",
}


def _make_redirect(target: str):
    def _redirect(request: Request) -> RedirectResponse:
        query = request.url.query
        return RedirectResponse(f"{target}?{query}" if query else target, status_code=301)
    return _redirect


for _old, _new in _LEGACY_REDIRECTS.items():
    router.add_api_route(_old, _make_redirect(_new), methods=["GET"], include_in_schema=False)


@router.get("/deck-edit/{deck_id}", include_in_schema=False)
def redirect_deck_edit_detail(deck_id: str) -> RedirectResponse:
    return RedirectResponse(f"/decks/{deck_id}", status_code=301)
