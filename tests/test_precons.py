"""Decks préconstruits : lecture des fichiers MTGJSON et routes protégées."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from manamind.precons import parse_deck


def _card(name: str, *, count: int = 1, foil: bool = False, subtypes: list | None = None,
          sid: str = "00000000-0000-0000-0000-000000000000") -> dict:
    return {"name": name, "count": count, "isFoil": foil, "setCode": "dsc",
            "number": "7", "subtypes": subtypes or [],
            "identifiers": {"scryfallId": sid}}


def test_un_deck_mtgjson_garde_commandant_cartes_et_jetons():
    deck = parse_deck({
        "name": "Death Toll", "code": "DSC", "releaseDate": "2024-09-27",
        "commander": [_card("Winter, Cynical Opportunist", foil=True)],
        "mainBoard": [_card("Sol Ring"), _card("Swamp", count=10)],
        "tokens": [_card("Insect", count=2)],
    })

    assert deck["set_code"] == "DSC"
    assert [c["name"] for c in deck["commanders"]] == ["Winter, Cynical Opportunist"]
    assert deck["commanders"][0]["is_foil"] is True
    assert sum(c["quantity"] for c in deck["main"]) == 11
    assert deck["tokens"][0]["zone"] == "token"


def test_carte_reversible_ramenee_a_son_nom():
    # Les Secret Lair réversibles s'écrivent « Sol Ring // Sol Ring » chez
    # MTGJSON : aucun écran ne reconnaîtrait ce nom.
    deck = parse_deck({
        "name": "Raining Cats and Dogs", "code": "SLD",
        "commander": [_card("Rin and Seri, Inseparable // Rin and Seri, Inseparable")],
        "mainBoard": [_card("Sol Ring // Sol Ring"), _card("Fire // Ice")],
    })

    assert deck["commanders"][0]["name"] == "Rin and Seri, Inseparable"
    assert [c["name"] for c in deck["main"]] == ["Sol Ring", "Fire // Ice"]


def test_trop_de_commandants_la_boite_decide():
    # Une boîte qui propose trois commandants au choix : celui mis en avant
    # reste commandant, les autres rejoignent le deck.
    deck = parse_deck({
        "name": "Trio", "code": "XXX",
        "commander": [_card("A"), _card("B"), _card("C")],
        "displayCommander": [_card("B")],
        "mainBoard": [_card("Sol Ring")],
    })

    assert [c["name"] for c in deck["commanders"]] == ["B"]
    assert {c["name"] for c in deck["main"]} == {"Sol Ring", "A", "C"}


def test_paire_de_commandants_conservee():
    deck = parse_deck({
        "name": "Paire", "code": "XXX",
        "commander": [_card("Rose Tyler"), _card("The Tenth Doctor")],
        "mainBoard": [],
    })
    assert len(deck["commanders"]) == 2


def test_liste_des_precons_exige_une_session(client: TestClient):
    assert client.get("/api/precons").status_code == 401


def test_ajout_d_un_precon_exige_une_session(client: TestClient):
    r = client.post("/api/precons/DeathToll_DSC/add", json={"add_to_collection": True})
    assert r.status_code == 401


def test_la_page_des_precons_est_servie(client: TestClient):
    r = client.get("/decks/precons")
    assert r.status_code == 200
    assert "EDHREC" not in r.text


def test_import_texte_vise_le_deck_cree_et_regroupe_les_doublons(monkeypatch):
    """Un deck importé ne doit plus écraser un ancien deck du même commandant."""
    import manamind.user_decks as user_decks
    from manamind.routers.import_deck import _save_to_deck

    calls: dict = {}
    monkeypatch.setattr(user_decks, "save_deck_for_user", lambda **kw: calls.update(save=kw))
    monkeypatch.setattr(user_decks, "set_deck_cards", lambda **kw: calls.update(cards=kw))

    entries = [
        SimpleNamespace(canonical_name="Forest", raw_name="Forest", quantity=10),
        SimpleNamespace(canonical_name="Forest", raw_name="Forest", quantity=5),
        SimpleNamespace(canonical_name="Sol Ring", raw_name="Sol Ring", quantity=1),
    ]
    imported, skipped, errors = _save_to_deck(1, "Omnath", "Mon deck", entries)

    assert errors == []
    assert calls["cards"]["deck_id"] == calls["save"]["deck_id"]
    assert dict(calls["cards"]["cards"]) == {"Forest": 15, "Sol Ring": 1}
