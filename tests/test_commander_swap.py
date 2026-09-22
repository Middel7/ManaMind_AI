"""Chargement des cartes du deck analysé pour un changement de commandant.

Une carte qui revient deux fois ici se dédouble partout en aval : la liste des
cartes conservées l'affiche deux fois, et elle compte deux fois dans la valeur
et dans le décompte. Deux chemins y menaient — deux decks portant le même
commandant, et deux écritures d'un même nom dans un seul deck.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


class _FausseSession:
    """Session minimale : retient les paramètres et rend les lignes fournies."""

    def __init__(self, lignes, journal):
        self._lignes = lignes
        self._journal = journal

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, requete, params=None):
        self._journal.append({"sql": str(requete), "params": params or {}})
        return self

    def fetchall(self):
        return self._lignes


@pytest.fixture
def charger(monkeypatch):
    """Appelle load_deck_cards sur des lignes données, sans base."""
    from manamind import commander_swap

    def _appel(lignes, **kwargs):
        journal = []
        rows = [SimpleNamespace(card_name=nom, quantity=qte) for nom, qte in lignes]
        monkeypatch.setattr(commander_swap, "SessionLocal",
                            lambda: _FausseSession(rows, journal))
        resultat = commander_swap.load_deck_cards(1, "Hope Estheim", **kwargs)
        return resultat, journal

    return _appel


def test_une_carte_ecrite_deux_fois_ne_compte_que_pour_une(charger):
    cartes, _ = charger([
        ("Aetherflux Reservoir", 1),
        ("aetherflux reservoir", 1),
        ("Sol Ring", 1),
    ])
    assert len(cartes) == 2, f"attendu 2 cartes distinctes, obtenu {cartes}"
    assert dict((nom.lower(), qte) for nom, qte in cartes)["aetherflux reservoir"] == 2, (
        "les exemplaires des deux écritures doivent s'additionner"
    )


def test_le_commandant_ne_figure_pas_dans_ses_propres_cartes(charger):
    cartes, _ = charger([("Hope Estheim", 1), ("Sol Ring", 1)])
    assert [nom for nom, _ in cartes] == ["Sol Ring"]


def test_le_deck_vise_prime_sur_le_nom_du_commandant(charger):
    """Deux decks peuvent porter le même commandant : l'identifiant tranche."""
    _, journal = charger([("Sol Ring", 1)], deck_id="abc123")
    assert journal, "aucune requête n'a été exécutée"
    assert journal[0]["params"].get("did") == "abc123"
    assert "deck_id" in journal[0]["sql"]


def test_sans_identifiant_la_recherche_reste_celle_du_commandant(charger):
    _, journal = charger([("Sol Ring", 1)])
    assert journal[0]["params"].get("cmd") == "Hope Estheim"
    assert "deck_id" not in journal[0]["sql"]


def test_les_quantites_absentes_valent_un(charger):
    cartes, _ = charger([("Sol Ring", None)])
    assert cartes == [("Sol Ring", 1)]
