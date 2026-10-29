"""Réglages de la buylist : decks analysés et cartes ajoutées à la main.

La base de test est un SQLite vide : le calcul lui-même se vérifie contre la
base locale. Restent ici le contrôle d'accès de chaque réglage.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_lire_les_decks_analyses_exige_une_session(client: TestClient):
    r = client.get("/api/v2/buylist/decks")
    assert r.status_code == 401, f"Attendu 401, obtenu {r.status_code}: {r.text}"


def test_choisir_les_decks_analyses_exige_une_session(client: TestClient):
    r = client.put("/api/v2/buylist/decks", json={"excluded": []})
    assert r.status_code == 401, f"Attendu 401, obtenu {r.status_code}: {r.text}"


def test_ajouter_une_carte_exige_une_session(client: TestClient):
    r = client.post("/api/v2/buylist/pinned", json={"card_name": "Sol Ring"})
    assert r.status_code == 401, f"Attendu 401, obtenu {r.status_code}: {r.text}"


def test_retirer_une_carte_exige_une_session(client: TestClient):
    r = client.delete("/api/v2/buylist/pinned", params={"card_name": "Sol Ring"})
    assert r.status_code == 401, f"Attendu 401, obtenu {r.status_code}: {r.text}"


def test_lire_les_cartes_ajoutees_exige_une_session(client):
    assert client.get("/api/v2/buylist/pinned").status_code == 401
