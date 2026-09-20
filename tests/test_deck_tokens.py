"""Les jetons d'un deck : ce que l'API expose, et a qui.

La suite tourne sans PostgreSQL — la base de test est un SQLite vide. Ces
tests portent donc sur ce qui se decide avant toute requete : personne ne lit
ni ne modifie les jetons d'un deck sans etre authentifie. Le calcul lui-meme
s'appuie sur scryfall_card_parts et se verifie contre la base locale.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

DECK = "un-deck-quelconque"


def test_lire_les_jetons_exige_une_session(client: TestClient):
    r = client.get(f"/api/v2/decks/{DECK}/tokens")
    assert r.status_code == 401, f"Attendu 401, obtenu {r.status_code}: {r.text}"


def test_ajouter_un_jeton_exige_une_session(client: TestClient):
    r = client.post(f"/api/v2/decks/{DECK}/tokens",
                    json={"key": "abc", "name": "Trésor"})
    assert r.status_code == 401, f"Attendu 401, obtenu {r.status_code}: {r.text}"


def test_retirer_un_jeton_exige_une_session(client: TestClient):
    r = client.delete(f"/api/v2/decks/{DECK}/tokens/abc")
    assert r.status_code == 401, f"Attendu 401, obtenu {r.status_code}: {r.text}"
