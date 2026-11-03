"""Collections à thème : traduction des critères et routes réservées."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from manamind import collection_store
from manamind.theme_collections import (
    CriteriaError, build_where, clean_definition, merge_by_illustration, opened_wanted)


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
    assert definition["by_illustration"] is False
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
    ("get", "/api/v2/themes/1/opened"),
])
def test_routes_fermees_sans_session(client: TestClient, method, url):
    response = getattr(client, method)(url)
    assert response.status_code == 401


def test_pages_servies(client: TestClient):
    assert client.get("/collection/themes").status_code == 200
    assert client.get("/collection/themes/3").status_code == 200


# ── Regroupement par illustration ────────────────────────────────────────────
# Impressions anglaises de Scalding Tarn relevées dans le catalogue le
# 2026-11-03, dans l'ordre de la collection (date décroissante) : extension,
# numéro, illustration, date de sortie. Les prix sont fixés pour le test :
# seul leur ordre compte.
_ZEN_ART = "68e3496e-d1f3-44d8-b524-4215608456a8"  # Philip Straub
_TARN = [
    ("SPG", "112", "4ad18651-5efb-4246-b01d-96d5ce4d77c6", "2025-04-11", 60.0),  # Adam Paquette
    ("SPG", "117", "5aeb3e95-f608-479a-a9d3-2c7e4e7fc7bc", "2025-04-11", 55.0),  # Marco Gorlei
    ("MH2", "254", _ZEN_ART, "2021-06-18", 22.0),
    ("MH2", "439", _ZEN_ART, "2021-06-18", 30.0),
    ("MH2", "478", _ZEN_ART, "2021-06-18", 25.0),
    ("PMH2", "254s", _ZEN_ART, "2021-05-06", None),
    ("ZNE", "7", "713eff05-bea9-467a-a74c-797797d2968e", "2020-09-25", 70.0),  # Jonas De Ro
    ("SLU", "2", "44dea293-671b-4d21-bc5a-e44e45424e8a", "2020-06-12", 45.0),  # Adam Paquette
    ("MM3", "244", _ZEN_ART, "2017-03-17", 24.0),
    ("EXP", "22", "7bfd995b-d460-4bb0-83b6-ec64e5536c3f", "2015-10-02", 150.0),  # Ryan Yee
    ("ZEN", "223", _ZEN_ART, "2009-10-02", 28.0),
]


def _edition(set_code, number, art, released, price, copies=0, declared=False,
             rarity="rare"):
    return {"set_code": set_code, "collector_number": number, "set_name": set_code,
            "illustration_id": art, "released_at": released, "price": price,
            "cardmarket_id": f"cm-{set_code}-{number}", "copies": copies,
            "declared": declared, "owned": bool(copies) or declared,
            "rarity": rarity, "scryfall_id": f"sf-{set_code}-{number}",
            "image_small": None, "image_normal": None}


def _zen(merged):
    return next(m for m in merged if m["illustration_id"] == _ZEN_ART)


def test_une_illustration_reunit_ses_editions():
    merged = merge_by_illustration([_edition(*row) for row in _TARN])
    # Onze éditions, six illustrations : celle de Zendikar couvre cinq extensions.
    assert len(merged) == 6
    assert [(e["set_code"], e["collector_number"]) for e in _zen(merged)["editions"]] == [
        ("MH2", "254"), ("MH2", "439"), ("MH2", "478"), ("PMH2", "254s"),
        ("MM3", "244"), ("ZEN", "223")]


def test_la_tuile_montre_la_plus_ancienne_et_achete_la_moins_chere():
    zen = _zen(merge_by_illustration([_edition(*row) for row in _TARN]))
    # L'image est celle de Zendikar (2009), où l'illustration est née…
    assert (zen["set_code"], zen["collector_number"]) == ("ZEN", "223")
    # … le prix et le lien, ceux de MH2 #254, la moins chère des six.
    assert zen["price"] == 22.0 and zen["cardmarket_id"] == "cm-MH2-254"
    assert (zen["buy_set_code"], zen["buy_collector_number"]) == ("MH2", "254")


def test_meme_artiste_ne_veut_pas_dire_meme_illustration():
    # Adam Paquette signe SPG 112 et SLU 2 : deux images, deux cases.
    merged = merge_by_illustration([_edition(*row) for row in _TARN])
    paquette = [m for m in merged if (m["set_code"], m["collector_number"])
                in {("SPG", "112"), ("SLU", "2")}]
    assert len(paquette) == 2
    assert all(len(m["editions"]) == 1 for m in paquette)


def test_une_edition_possedee_suffit_a_son_illustration():
    # Un exemplaire MH2 #478 rend possédée l'illustration montrée sous ZEN.
    rows = [_edition(*row, copies=2 if row[:2] == ("MH2", "478") else 0) for row in _TARN]
    merged = merge_by_illustration(rows)
    zen = _zen(merged)
    assert zen["owned"] and zen["copies"] == 2
    assert zen["set_code"] == "ZEN"
    assert sum(m["owned"] for m in merged) == 1


def test_une_declaration_existante_vaut_pour_l_illustration():
    # « Annuler possédée » doit retirer la déclaration là où elle a été faite.
    rows = [_edition(*row, declared=row[:2] in {("MM3", "244"), ("MH2", "439")})
            for row in _TARN]
    zen = _zen(merge_by_illustration(rows))
    assert zen["declared"] and zen["owned"]
    assert [(e["set_code"], e["collector_number"]) for e in zen["editions"]
            if e["declared"]] == [("MH2", "439"), ("MM3", "244")]


def test_une_illustration_inconnue_ne_regroupe_rien():
    # Island TRC 9999 et SLD 255 n'ont pas d'illustration_id au catalogue ;
    # TRK 320 et 562 partagent la même image.
    art = "a08d4ecd-659a-4d26-8c5e-6f97e56469fa"
    islands = [
        _edition("TRK", "562", art, "2026-11-13", 0.5),
        _edition("TRC", "9999", None, "2026-11-13", None),
        _edition("TRK", "320", art, "2026-11-13", 0.3),
        _edition("SLD", "255", None, "2021-04-26", 4.0),
    ]
    merged = merge_by_illustration(islands)
    assert [len(m["editions"]) for m in merged] == [2, 1, 1]
    assert [m["set_code"] for m in merged] == ["TRK", "TRC", "SLD"]


class _Session:
    """Répond à la seule question posée : la colonne existe-t-elle ?"""

    def __init__(self, present):
        self.present = present

    def execute(self, *_args, **_kwargs):
        return self

    def scalar(self):
        return 1 if self.present else None


@pytest.mark.parametrize("present,expected", [
    (True, "p.illustration_id::text"), (False, "NULL::text")])
def test_illustration_lue_seulement_si_la_colonne_existe(monkeypatch, present, expected):
    monkeypatch.setattr(collection_store, "_PRINTING_COLUMNS", {})
    assert collection_store.printing_illustration_sql(_Session(present)) == expected


# ── Boosters ouverts ─────────────────────────────────────────────────────────
# Serra Angel, impressions anglaises relevées le 2026-11-03 : trois
# illustrations (Greg Staples, Donato Giancola, Douglas Shuler).
_STAPLES = "5f5651af-22fb-4fb8-a04c-1f34b5104bd9"
_GIANCOLA = "7aa16de3-4ff9-4ed9-9d62-62c140599508"
_SHULER = "6eb98f0e-e947-4efd-a84f-a4b20de70872"
_SERRA = [
    ("FDN", "147", _STAPLES, "2024-11-15", 0.1),
    ("DMR", "25", _GIANCOLA, "2023-01-13", 0.2),
    ("DMR", "271", _GIANCOLA, "2023-01-13", 0.3),
    ("DOM", "33", _GIANCOLA, "2018-04-27", 0.2),
    ("M15", "273", _STAPLES, "2014-07-18", 0.2),
    ("2ED", "40", _SHULER, "1993-12-01", 70.0),
]


def _serra(copies=None, by_illustration=True):
    copies = copies or {}
    editions = [_edition(*row, copies=copies.get(row[:2], 0), rarity="uncommon")
                for row in _SERRA]
    if by_illustration:
        editions = merge_by_illustration(editions)
    return [{"card_id": 1, "name": "Serra Angel", "local_name": "Ange de Serra",
             "editions": editions}]


def test_boosters_ouverts_ranges_par_extension():
    sets = opened_wanted(_serra(), {"fdn", "DMR"})
    assert [(s["set_code"], [c["collector_number"] for c in s["cards"]]) for s in sets] == [
        ("FDN", ["147"]), ("DMR", ["25", "271"])]
    assert sets[0]["cards"][0]["local_name"] == "Ange de Serra"


def test_boosters_ouverts_ecartent_une_illustration_possedee_ailleurs():
    # Un exemplaire DOM #33 possède l'image de Giancola : DMR n'a plus rien à chercher.
    sets = opened_wanted(_serra({("DOM", "33"): 1}), {"FDN", "DMR"})
    assert [s["set_code"] for s in sets] == ["FDN"]


def test_boosters_ouverts_par_edition_sans_regroupement():
    # Sans comptage par illustration, seule l'édition elle-même compte.
    sets = opened_wanted(_serra({("DOM", "33"): 1}, by_illustration=False), {"DMR"})
    assert [c["collector_number"] for c in sets[0]["cards"]] == ["25", "271"]


def test_boosters_ouverts_seulement_communes_et_peu_communes():
    cards = [{"card_id": 2, "name": "Scalding Tarn", "local_name": None,
              "editions": merge_by_illustration([_edition(*row) for row in _TARN])}]
    assert opened_wanted(cards, {"MH2", "ZEN"}) == []
