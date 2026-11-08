"""CSV des recommandations par popularité, tel que la page d'analyse le lit."""
from __future__ import annotations

import csv

from manamind.recommandation_populaire import save_recommendations


def test_les_ajouts_portent_leur_part_des_decks(tmp_path):
    path = tmp_path / "reco.csv"
    save_recommendations(path, "Caesar, Legion's Emperor",
                         [("Sol Ring", 577, 79.34)], [("Selfless Spirit", 2, 0.21)])

    rows = {r["Card Name"]: r for r in csv.DictReader(path.open(encoding="utf-8"))}
    assert rows["Sol Ring"]["Deck Frequency"] == "79.3"
    assert rows["Sol Ring"]["Support"] == "577"
    assert rows["Selfless Spirit"]["Deck Frequency"] == "0.2"
