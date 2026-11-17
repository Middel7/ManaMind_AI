"""« Améliorer avec ma collection » est devenu un filtre de l'analyse par popularité."""


def test_ameliorer_avec_un_deck_lance_l_analyse_filtre_pose(client):
    response = client.get("/decks/ameliorer?deck=ab c/1", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == (
        "/decks/analyse?deck=ab%20c%2F1&run=popular&collection=1")


def test_ameliorer_sans_deck_ouvre_le_choix_filtre_pose(client):
    response = client.get("/decks/ameliorer", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/decks/analyse?collection=1"
