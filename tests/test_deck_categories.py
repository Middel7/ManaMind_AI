"""Tests de l'analyse poussée : catégories de tags et comparaison aux decks similaires."""

from manamind.deck_categories import CATEGORIES, categorize, is_basic_land, summarize


def test_terrains_de_base_reconnus():
    for name in ("Forest", "island", " Snow-Covered Swamp ", "Wastes"):
        assert is_basic_land(name), name
    for name in ("Breeding Pool", "Forest Bear", "Command Tower"):
        assert not is_basic_land(name), name


def test_cinquante_categories_distinctes():
    assert len(CATEGORIES) == 50
    assert len(set(CATEGORIES)) == 50


def test_tags_sans_role_hors_categorie():
    # Cycles, curiosités de nom, règles, capacités trop générales, impressions.
    for tag in ("cycle-rav-shockland", "alliteration", "namesake spell",
                "errata", "activated ability", "oversized", "unique token",
                "artwork series land"):
        assert categorize(tag) is None, tag


def test_tags_ranges_par_role():
    attendu = {
        "draw engine": "Pioche de cartes",
        "mana rock": "Production de mana",
        "land ramp": "Recherche de terrains",
        "tutor-land-basic": "Recherche de terrains",
        "tutor-to-hand": "Tuteurs",
        "spot removal": "Destruction ciblée",
        "sweeper": "Destruction de masse",
        "flicker-creature": "Blink et arrivées",
        "extra turn": "Tours et phases supplémentaires",
        "alternate win condition": "Conditions de victoire",
        "synergy-vehicle": "Véhicules",
        "synergy-equipment": "Équipements",
        "synergy-artifact": "Artefacts",
        "synergy-saga": "Sagas",
        "synergy-aura": "Auras",
        "synergy-enchantment": "Enchantements",
    }
    for tag, categorie in attendu.items():
        assert categorize(tag) == categorie, tag


def test_faux_amis_ecartes():
    # Sous-chaînes trompeuses : « draw » dans drawback, « ramp » dans trample,
    # « extra » dans extract, « flip » dans coin flip, « hate- » qui combat x.
    assert categorize("drawback") != "Pioche de cartes"
    assert categorize("gives trample") != "Production de mana"
    assert categorize("extract") is None
    assert categorize("coin flip") == "Hasard, dés et pièces"
    assert categorize("hate-graveyard") == "Anti-stratégies (hate)"
    assert categorize("fetchable") == "Terrains et synergies de terrain"


def test_resume_classe_selon_les_decks_similaires():
    tags = {
        "Sol Ring": ["mana rock", "fast mana"],
        "Rhystic Study": ["draw engine"],
        "Swords to Plowshares": ["spot removal"],
        "Forest": ["fetchable", "cycle-lea-basic-land"],
    }
    deck = {"Sol Ring": 1, "Forest": 30}
    # Sur 10 decks : 10 Sol Ring, 8 Rhystic Study, 5 Swords, 250 Forêts.
    population = {"Sol Ring": 10, "Rhystic Study": 8,
                  "Swords to Plowshares": 5, "Forest": 250}

    rows = summarize(deck, population, 10, tags)

    # Le classement, puis l'interaction toujours en dessous, même à zéro.
    assert [row["category"] for row in rows] == [
        "Terrains et synergies de terrain", "Production de mana",
        "Pioche de cartes",
        "Destruction ciblée", "Destruction de masse", "Contresorts"]
    assert [row["always"] for row in rows] == [False] * 3 + [True] * 3
    terrains, mana, pioche, retrait, masse, contresorts = rows
    assert (terrains["deck"], terrains["similar"]) == (30, 25.0)
    # Deux tags de la même catégorie ne comptent la carte qu'une fois.
    assert mana["deck"] == 1 and mana["similar"] == 1.0
    assert pioche["deck"] == 0 and pioche["similar"] == 0.8
    assert retrait["similar"] == 0.5
    assert (masse["deck"], masse["similar"], masse["cards"]) == (0, 0.0, [])
    assert contresorts["similar"] == 0.0


def test_cartes_du_deck_et_cartes_manquantes():
    tags = {
        "Sol Ring": ["mana rock"],
        "Arcane Signet": ["mana rock"],
        "Mind Stone": ["mana rock"],
        "Fellwar Stone": ["mana rock"],
        "Bloodline Keeper // Lord of Lineage": ["repeatable creature tokens"],
    }
    # Le deck écrit la carte recto-verso par sa face avant, en minuscules.
    deck = {"Sol Ring": 1, "Fellwar Stone": 1, "bloodline keeper": 1}
    population = {"Sol Ring": 10, "Arcane Signet": 9, "Mind Stone": 4,
                  "Bloodline Keeper // Lord of Lineage": 2}

    rows = {row["category"]: row for row in summarize(deck, population, 10, tags)}

    mana = rows["Production de mana"]
    # Du moins joué ailleurs au plus joué ; Fellwar Stone n'est joué nulle part.
    assert mana["cards"] == [{"card_name": "Fellwar Stone", "rate": 0.0},
                             {"card_name": "Sol Ring", "rate": 100.0}]
    # Les absentes, de la plus jouée à la moins jouée.
    assert mana["missing"] == [{"card_name": "Arcane Signet", "rate": 90.0},
                               {"card_name": "Mind Stone", "rate": 40.0}]
    # La face avant suffit à reconnaître la carte du deck.
    assert rows["Jetons"]["missing"] == []


def test_resume_limite_et_sans_decks_similaires():
    tags = {f"Carte {i}": [tag] for i, tag in enumerate(
        ["mana rock", "draw engine", "spot removal", "sweeper"])}
    population = {name: 1 for name in tags}
    # Les catégories d'interaction ne prennent pas de place dans le top : il
    # garde ses deux catégories, et les trois suivent.
    rows = summarize({}, population, 1, tags, top=2)
    assert [row["category"] for row in rows] == [
        "Pioche de cartes", "Production de mana",
        "Destruction ciblée", "Destruction de masse", "Contresorts"]
    assert summarize({"Carte 0": 1}, {}, 0, tags) == []
