"""Bracket d'un deck : le plancher calcule, et qui peut le corriger.

La base de test est un SQLite vide : la lecture des cartes sensibles se
verifie contre la base locale. Restent ici les seuils du calcul et le
controle d'acces de la correction manuelle.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from manamind.deck_bracket import bracket_summary, computed_bracket

DECK = "un-deck-quelconque"


def test_un_deck_sans_carte_sensible_est_en_bracket_2():
    assert computed_bracket(0, 0, 0) == 2


def test_un_a_trois_game_changers_donnent_le_bracket_3():
    assert computed_bracket(1, 0, 0) == 3
    assert computed_bracket(3, 0, 0) == 3


def test_un_quatrieme_game_changer_donne_le_bracket_4():
    assert computed_bracket(4, 0, 0) == 4


def test_une_seule_destruction_massive_de_terrains_donne_le_bracket_4():
    assert computed_bracket(0, 1, 0) == 4


def test_les_tours_supplementaires_comptent_a_partir_de_trois():
    assert computed_bracket(0, 0, 2) == 2
    assert computed_bracket(0, 0, 3) == 4


def test_le_choix_manuel_passe_avant_le_calcul():
    flags = {"game_changers": ["Rhystic Study"], "mass_land_denial": [],
             "extra_turns": []}
    summary = bracket_summary(flags, 1)
    assert summary == {"value": 1, "computed": 3, "manual": True}


def test_sans_choix_manuel_le_calcul_fait_foi():
    assert bracket_summary(None, None) == {"value": 2, "computed": 2, "manual": False}


def test_corriger_le_bracket_exige_une_session(client: TestClient):
    r = client.post(f"/api/v2/decks/{DECK}/bracket", json={"bracket": 3})
    assert r.status_code == 401, f"Attendu 401, obtenu {r.status_code}: {r.text}"
