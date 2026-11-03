"""Collections à thème : traduction des critères et routes réservées."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from manamind.theme_collections import CriteriaError, build_where, clean_definition


def test_les_criteres_se_combinent_par_et():
    where, params = build_where([
        {"field": "type", "value": "Angel"},
        {"field": "mana_value", "op": "eq", "value": "4"},
    ])
    assert where == "(c.type_line ILIKE :k0) AND (c.mana_value = :k1)"
    assert params == {"k0": "%Angel%", "k1": 4.0}


def test_sauf_garde_les_cartes_sans_valeur():
    # Un rituel n'a pas de force : « sauf force ≥ 7 » doit le garder.
    where, _ = build_where([{"field": "power", "op": "gte", "value": 7, "not": True}])
    assert where.startswith("NOT COALESCE((")
    assert where.endswith("), FALSE)")


def test_aucune_saisie_dans_le_texte_sql():
    malicious = "x'; DROP TABLE users; --"
    where, params = build_where([{"field": "name", "value": malicious}])
    assert "DROP" not in where
    assert params["k0"] == f"%{malicious}%"


def test_couleurs_exactes_au_moins_au_plus():
    exact, params = build_where([{"field": "colors", "op": "exact", "value": ["w", "U"]}])
    assert "@>" in exact and "<@" in exact
    assert params["k0"] == ["U", "W"]
    include, _ = build_where([{"field": "identity", "op": "include", "value": ["G"]}])
    assert "c.color_identity @>" in include and "<@" not in include
    colorless, params = build_where([{"field": "colors", "value": ["C"]}])
    assert "cardinality(c.colors), 0) = 0" in colorless and params == {}


def test_extensions_et_raretes_en_liste():
    where, params = build_where([{"field": "set", "value": ["DMU", " m10 "]},
                                 {"field": "rarity", "value": ["mythic"]}])
    assert "LOWER(p.set_code) = ANY(:k0)" in where
    assert params["k0"] == ["dmu", "m10"]
    assert params["k1"] == ["mythic"]


@pytest.mark.parametrize("criteria", [
    [],
    [{"field": "inconnu", "value": "x"}],
    [{"field": "type", "value": "  "}],
    [{"field": "mana_value", "op": "eq", "value": "quatre"}],
    [{"field": "mana_value", "op": "~", "value": 4}],
    [{"field": "colors", "value": ["X"]}],
    [{"field": "rarity", "value": []}],
    "type:angel",
])
def test_criteres_invalides_refuses(criteria):
    with pytest.raises(CriteriaError):
        build_where(criteria)


def test_definition_nettoyee():
    definition = clean_definition({"name": "  Les anges ", "language": "FR",
                                   "criteria": [{"field": "type", "value": "Angel"}]})
    assert definition["name"] == "Les anges"
    assert definition["language"] == "fr"
    assert definition["include_alt"] is False
    with pytest.raises(CriteriaError):
        clean_definition({"name": "", "criteria": [{"field": "type", "value": "Angel"}]})
    with pytest.raises(CriteriaError):
        clean_definition({"name": "x", "language": "klingon",
                          "criteria": [{"field": "type", "value": "Angel"}]})


@pytest.mark.parametrize("method,url", [
    ("get", "/api/v2/themes"),
    ("get", "/api/v2/themes/1"),
    ("post", "/api/v2/themes"),
    ("post", "/api/v2/themes/preview"),
    ("delete", "/api/v2/themes/1"),
    ("post", "/api/v2/themes/1/buylist"),
    ("post", "/api/v2/themes/1/editions"),
])
def test_routes_fermees_sans_session(client: TestClient, method, url):
    response = getattr(client, method)(url)
    assert response.status_code == 401


def test_pages_servies(client: TestClient):
    assert client.get("/collection/themes").status_code == 200
    assert client.get("/collection/themes/3").status_code == 200
