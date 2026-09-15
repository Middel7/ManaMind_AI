"""Tests du format canonique des commandants.

Ce format n'est pas cosmetique : les statistiques d'inclusion sont indexees
par la chaine « A & B » triee. Une paire ecrite autrement, ou amputee de l'un
de ses deux noms, ne trouve aucune statistique et laisse l'analyse muette.
"""
from __future__ import annotations

from manamind.commanders import (
    is_commander,
    join_commanders,
    split_commanders,
)

KRARK = "Krark, the Thumbless"
SAKASHIMA = "Sakashima of a Thousand Faces"
PAIR = f"{KRARK} & {SAKASHIMA}"


class TestSplit:
    def test_un_seul_commandant(self):
        assert split_commanders(SAKASHIMA) == [SAKASHIMA]

    def test_paire(self):
        assert split_commanders(PAIR) == [KRARK, SAKASHIMA]

    def test_vide(self):
        assert split_commanders(None) == []
        assert split_commanders("") == []
        assert split_commanders("   ") == []

    def test_espaces_autour_du_separateur(self):
        assert split_commanders(f"{KRARK}&{SAKASHIMA}") == [KRARK, SAKASHIMA]

    def test_une_carte_recto_verso_reste_un_commandant(self):
        dfc = "Cosima, God of the Voyage // The Omenkeel"
        assert split_commanders(dfc) == [dfc]


class TestJoin:
    def test_ordre_alphabetique_quel_que_soit_l_ordre_donne(self):
        assert join_commanders([SAKASHIMA, KRARK]) == PAIR
        assert join_commanders([KRARK, SAKASHIMA]) == PAIR

    def test_doublon_ecarte(self):
        assert join_commanders([KRARK, KRARK]) == KRARK
        assert join_commanders([KRARK, KRARK.lower()]) == KRARK

    def test_nom_vide_ignore(self):
        assert join_commanders([KRARK, "", None]) == KRARK

    def test_nom_portant_un_esperluette_garde_l_ordre_donne(self):
        # « Leo, Chaos & Order » rendrait le redecoupage ambigu : on ne trie
        # pas, mais on ne perd aucun des deux noms.
        leo = "Leo, Chaos & Order"
        assert join_commanders([leo, KRARK]) == f"{leo} & {KRARK}"

    def test_aller_retour(self):
        assert split_commanders(join_commanders([SAKASHIMA, KRARK])) == [KRARK, SAKASHIMA]


class TestIsCommander:
    def test_chaque_nom_de_la_paire(self):
        assert is_commander(PAIR, KRARK)
        assert is_commander(PAIR, SAKASHIMA)

    def test_casse_ignoree(self):
        assert is_commander(PAIR, KRARK.upper())

    def test_carte_ordinaire(self):
        assert not is_commander(PAIR, "Lightning Bolt")
        # Une carte dont le nom ressemble a celui du commandant sans l'etre.
        assert not is_commander(PAIR, "Krark's Thumb")

    def test_deck_sans_commandant(self):
        assert not is_commander(None, KRARK)
        assert not is_commander("", KRARK)
