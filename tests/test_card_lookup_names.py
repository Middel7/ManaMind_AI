"""Une carte recto-verso se cherche sous son nom complet et sa face avant."""
from manamind.card_commander_matcher import card_lookup_names


def test_recto_verso_cherche_aussi_sa_face_avant():
    # Les decks scrapés n'en gardent que la face avant : sans elle, la page
    # « Dans quel commandant jouer cette carte ? » ne trouvait rien.
    assert card_lookup_names("Wandering Archaic // Explore the Vastlands") == [
        "Wandering Archaic // Explore the Vastlands", "Wandering Archaic",
    ]


def test_carte_simple_un_seul_nom():
    assert card_lookup_names("  Sol Ring ") == ["Sol Ring"]
