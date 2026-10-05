"""Choix de la suggestion Moxfield pour le filtre Commander."""
import pytest

pytest.importorskip("playwright")

from manamind.moxfield_scraper.links import pick_suggestion, split_commander  # noqa: E402

VALKI = "Valki, God of Lies // Tibalt, Cosmic Impostor"


def test_carte_recto_verso_retenue_sous_son_nom_complet():
    """La première suggestion répète la face avant : ce n'est pas le commandant."""
    suggestions = ["Valki, God of Lies // Valki, God of Lies", VALKI]
    assert pick_suggestion(suggestions, VALKI) == 1


def test_le_filtre_tape_la_face_avant_et_vise_le_nom_complet():
    front, back, is_dfc = split_commander(VALKI)
    assert (front, is_dfc) == ("Valki, God of Lies", True)
    assert f"{front} // {back}" == VALKI


def test_espacement_et_casse_ignores():
    suggestions = ["Valki, God of Lies // Valki, God of Lies",
                   "valki, god of lies//Tibalt, Cosmic Impostor"]
    assert pick_suggestion(suggestions, VALKI) == 1


def test_face_repetee_ecartee_sans_correspondance_exacte():
    """Sans le nom complet dans la liste, une face répétée n'est jamais retenue."""
    suggestions = ["Esika, God of the Tree // Esika, God of the Tree",
                   "Esika, God of the Tree // The Prismatic Bridge"]
    assert pick_suggestion(suggestions, "Esika, God of the Tree") == 1


def test_carte_simple_et_liste_vide():
    assert pick_suggestion(["Sol Ring"], "Sol Ring") == 0
    assert pick_suggestion(["Krenko, Mob Boss", "Krenko, Tin Street Kingpin"],
                           "Krenko, Tin Street Kingpin") == 1
    assert pick_suggestion([], "Sol Ring") == 0
