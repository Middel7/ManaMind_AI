"""Tests de l'installation sur l'écran d'accueil.

Chrome sur Android ne propose « Installer l'application » qu'à trois
conditions : un manifeste complet, des icônes de 192 et 512 px, et un service
worker de portée racine qui intercepte les requêtes. Si l'une tombe, le menu
retombe silencieusement sur « Créer un raccourci » — une panne sans message
d'erreur, que seuls ces tests rattrapent.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def test_manifeste_servi_avec_son_type(client: TestClient):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")


def test_manifeste_complet(client: TestClient):
    manifeste = json.loads(client.get("/manifest.webmanifest").text)
    for champ in ("name", "short_name", "start_url", "scope", "icons"):
        assert manifeste.get(champ), f"champ « {champ} » manquant"
    assert manifeste["display"] in {"standalone", "fullscreen", "minimal-ui"}
    assert not manifeste.get("prefer_related_applications")


def test_manifeste_porte_les_deux_tailles_exigees(client: TestClient):
    icones = json.loads(client.get("/manifest.webmanifest").text)["icons"]
    tailles = {icone["sizes"] for icone in icones}
    assert {"192x192", "512x512"} <= tailles
    assert any("maskable" in icone.get("purpose", "") for icone in icones), (
        "sans icône maskable, Android rogne l'icône dans un carré blanc"
    )


def test_icones_du_manifeste_existent(client: TestClient):
    icones = json.loads(client.get("/manifest.webmanifest").text)["icons"]
    for icone in icones:
        r = client.get(icone["src"])
        assert r.status_code == 200, f"{icone['src']} introuvable"
        assert r.headers["content-type"] == "image/png"


def test_service_worker_a_la_racine(client: TestClient):
    """Servi depuis « / » : un worker ne contrôle que ce qui est sous lui."""
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert "addEventListener('fetch'" in r.text, (
        "sans gestionnaire fetch, Chrome ne juge pas le site installable"
    )


def test_pages_declarent_le_manifeste():
    pages = sorted(ROOT.glob("*.html"))
    assert pages, "aucune page HTML trouvée"
    for page in pages:
        texte = page.read_text(encoding="utf-8")
        assert 'rel="manifest"' in texte, f"{page.name} ne lie pas le manifeste"
        assert 'name="theme-color"' in texte, f"{page.name} n'a pas de theme-color"


def test_socle_enregistre_le_service_worker():
    mm = (ROOT / "static" / "js" / "mm.js").read_text(encoding="utf-8")
    assert "serviceWorker.register('/sw.js'" in mm
