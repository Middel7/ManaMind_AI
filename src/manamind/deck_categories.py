"""deck_categories.py — Ce que fait un deck, rangé en 50 catégories de rôle.

Scryfall décrit chaque carte par des tags de fonction (« spot removal »,
« draw engine »…) : plus de 4 600 dans les decks Commander, bien trop pour se
lire. Ils sont regroupés ici en 50 catégories de rôle — pioche, production de
mana, retrait ciblé… — par des règles sur leur nom.

Une partie des tags ne dit rien du rôle d'une carte : son nom, sa série, sa
rédaction, ou une capacité trop générale (« activated ability »). Ceux-là
n'entrent dans aucune catégorie.

L'analyse poussée compare un deck aux decks publics du même commandant : pour
chaque catégorie, le nombre de cartes du deck et le nombre moyen de cartes par
deck chez les autres. Une carte compte une fois dans chaque catégorie qu'elle
touche, quel que soit le nombre de ses tags dans cette catégorie. Les terrains
de base sont écartés des deux côtés : les statistiques des decks similaires ne
disent pas en combien d'exemplaires ils sont joués.
"""

from __future__ import annotations

import re
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.orm import Session

from manamind.commanders import commander_keys, is_commander

# Nombre de catégories montrées par l'analyse poussée.
TOP = 15
# Cartes jouées ailleurs et absentes du deck, proposées par catégorie.
MISSING = 12

# Tags qui décrivent la carte plutôt que son rôle : cycles de cartes, conception
# et règles, univers et curiosités de nom. Testés avant les catégories, faute
# de quoi « namesake spell » passerait pour un sort et « artwork series land »
# pour un terrain.
_EXCLUDED = re.compile("|".join([
    # Cycles de cartes : une série, pas une fonction.
    r"^cycle[- ]",
    # Conception, règles et errata.
    r"signpost|draft|^precon|^pair-|notorious templating|faux targeting"
    r"|gonti clause|errata|ruling|reminder|text|rules|untracked|unnoted"
    r"|intervening|reflexive|passive ability|self-replacement"
    r"|cast on resolution|exile on resolution|unique counter|virtual",
    # Univers, noms et curiosités.
    r"name|misnomer|anagram|alliteration|\bart\b|artwork|masterpiece|\bpun"
    r"|dnd|40k|ffvii|fin-fic|the doctor|the ring|cameo|maro|gary"
    r"|guest designer|vanity|invitational|player spotlight|futureshift"
    r"|post release|you make the card|core-settable|roman numeral|owl"
    r"|squirrel|cheerio|meme|rhym|portmanteau|reference|nicknamed|^egg"
    r"|flavor|storyline|curiosity|aesthetic|uninspired|unique type line"
    r"|unique mana cost|eponymous|sticker|^\dpoints?$|^1point|^\dph$|fun "
    r"|joke|bible|\btome\b|mix-and-match|signature|french$|\bold\b|aikido"
    r"|tormenting|^titan trigger|humble|wild pair|impact effect"
    r"|mixed subtypes|dynaheirable|onomatopoeia|inscryption",
    # L'objet imprimé plutôt que la carte jouée : format géant, version jeton.
    # Sans cela, une Forêt passait pour une créature et Llanowar Elves pour un
    # générateur de jetons.
    r"oversized|booster|identical token|token version|unique token"
    r"|shares name with a token|named token",
]))

# (catégorie, motif) : la première règle qui reconnaît le tag l'emporte. L'ordre
# compte — « tutor-land » est de la recherche de terrains avant d'être un
# tuteur, « removal-bounce » un retrait avant d'être un renvoi en main.
_RULES = [
    ("Tribal (typal)", r"typal|^lord|changeling|sliver|type addition|tribal"),
    # Un tag « hate-x » combat x : compté plus bas, il passerait pour x.
    ("Anti-stratégies (hate)", r"hate"),
    # Avant le combat : « extra combat phase » est un tour de plus, pas une
    # évasion.
    ("Tours et phases supplémentaires", r"extra (turn|upkeep|combat|phase)"
        r"|additional (turn|combat|phase)|turns taken|skip turn"),
    # « fetchable » marque un terrain qu'on peut aller chercher : il range les
    # terrains de base avec les terrains, pas avec la recherche de terrains.
    ("Recherche de terrains", r"tutor-land|land ramp|landcycl|rampant"
        r"|land conversion|play additional land"),
    ("Tuteurs", r"tutor|wish"),
    ("Impulsion et exil-pioche", r"impuls|plunder|consult|cascade|discover"),
    ("Pioche de cartes", r"divination|fact or fiction|compulsive research|\bsift"
        r"|gifts ungiven|dark confidant|draw(?!back)|cantrip|card advantage"
        r"|loot|rummag|wheel|life for cards|hand size|hand-positive|hand-neutral"
        r"|burst"),
    ("Défausse", r"discard|madness|ditch|hand-negative"),
    ("Production de mana", r"mana sink|moxen|mana storage|mana egg|mana source"
        r"|energy|mana gorger|mana abilities|mana rock|mana dork|ritual"
        r"|adds .*mana|fast mana|mana ability|mana producer|treasure|(?<!t)ramp"
        r"|mana fix|buff mana|restricted mana|non mana ability mana"
        r"|mana increaser|sol land|signet|talisman|filter"),
    ("Réduction et coûts alternatifs", r"reducer|cheaper|discount|affinity"
        r"|free-cast|free spell"
        r"|alternate cost|alt cost|alt-cost|refund|cost ignorer|convoke|delve"
        r"|improvise|evoke|emerge|phyrexian mana|more expensive|cast tax|cost"),
    ("Contresorts", r"counterspell|interrupt|remove-from-stack|change target"
        r"|uncounterable|counter target"),
    ("Destruction de masse", r"sweeper|wrath|mass removal|multi removal"
        r"|board wipe|^mass(?! reanimation)"),
    ("Bounce et renvoi en main", r"bounce|tuck"),
    ("Retrait ciblé", r"doom blade|banish|abrade|disintegrate|pacifism|o-ring"
        r"|death-to-exile|frost armor|shrink|removal|disenchant"
        r"|naturalize|saboteur|swap removal|deanimate"),
    ("Dégâts directs", r"burn|pinger|damage(?! prevention)|fight|bite"),
    ("Perte de vie et drain", r"poison|afflict|set life total|life divider"
        r"|deal with the devil|loses life|life loss|drain|blood artist"
        r"|group slug|life payment|pay life"),
    ("Gain de vie", r"lifegain|lifelink|soul warden|gain life"),
    ("Sacrifice et morts", r"morbid|grave pact|devour|pariah|sacrifice"
        r"|death trigger|martyr|aristocrat|dies|cheat death|crime"),
    ("Marqueurs +1/+1 et compteurs", r"pp counter|mm counter|counters?\b"
        r"|proliferat|counter doubler"),
    ("Jetons", r"token|food|clue|blood|multiple bodies|enters in company"),
    ("Renforts et anthems", r"giant growth|overrun|enlarge|firebreathing"
        r"|pridemate|trumpet blast|ethereal armor|anthem|boost|buff|pump"
        r"|combat trick"),
    ("Capacités conférées", r"^gives|^gains|grants|keyword"),
    ("Protection", r"protect|hexproof|indestructible|shroud|ward|regenerat|fog"
        r"|prevent|phasing|phase out"),
    ("Évasion et combat", r"ninjutsu|exalted|raid|battalion|battle cry|enrage"
        r"|skulk|lure|marauder|daunt|stalking|provoke|outnumber|evasion"
        r"|unblockable|attack|combat|block|trample|menace|flying|strike"
        r"|vigilance|haste|landwalk|reach|deathtouch|defender|ninja|goad"),
    ("Réanimation", r"reanimat|cheat into play|sneak|put onto battlefield"
        r"|onto-battlefield|to-battlefield"),
    ("Récupération du cimetière", r"persist|undergrowth|necromancy|eternalize"
        r"|archaeomancer|phoenix|recycle|crucible|regrowth|recursion|graveyard"
        r"|flashback|escape|unearth|retrace|from exile|castable from|restock"
        r"|dredge"),
    ("Meule", r"mill"),
    ("Blink et arrivées", r"flicker|blink|etb|enters|leaves"),
    ("Copie et clones", r"copy|clone|doubler|twin|populate|replicate"),
    ("Vol et échanges de contrôle", r"threaten|bribery|defector|ransom|theft"
        r"|steal|donate|exchange|control"),
    ("Engager et dégager", r"tapper|untap|tap fuel|tap ability|lockdown|freeze"
        r"|stun"),
    ("Taxes et verrouillage", r"silence|null rod|rule of law|stasis|pillowfort"
        r"|abyss|\btax|rhystic|toll|restriction|stax|prison|drawback|symmetrical"),
    ("Multijoueur et politique", r"group hug|multiplayer|per-player|vote"
        r"|monarch|initiative|opponent|catch up|selective|outside commander"
        r"|politic"),
    ("Commandant et légendaires", r"commander|legend|partner|background"
        r"|companion"),
    ("Arpenteurs", r"planeswalker|\bpw\b|loyalty|superfriends"),
    ("Véhicules", r"vehicle|crew|spacecraft"),
    ("Équipements", r"equipment|equip|living weapon|cranial plating"),
    ("Artefacts", r"metalcraft|karnstruct|construct|thopter|artifact"),
    ("Sagas", r"saga"),
    ("Auras", r"aura|curse|role"),
    ("Enchantements", r"enchantment|room|shrine"),
    ("Éphémères, rituels et sorts", r"heroic|strive|spree|surge|spectacle"
        r"|addendum|converge|confluence|multiple targets|instant|sorcery|spell"
        r"|storm|cast trigger|magecraft|prowess|lesson|charm|split"),
    ("Terrains et synergies de terrain", r"land|fetch|dual|creaturefall|island"
        r"|forest|swamp|plains|mountain|desert|basic|gate"),
    ("Couleurs et identité", r"color|mono|hybrid|phyrexian|colorless|multicolor"
        r"|guild|shard|wedge|allied|enemy|\bwhite|\bblue|\bblack|\bred\b"
        r"|\bgreen|snow|devotion|rainbow"),
    ("Caractéristiques des créatures", r"ferocious|lhurgoyf|flowstone"
        r"|unique p/t|over-statted|vanilla|oversized|bear|wall|power|toughness"
        r"|cda|creature count|creature-ability|\d/\d|size|big|small|creature"),
    # Avant les doubles faces : « coin flip » est un pile ou face.
    ("Hasard, dés et pièces", r"dice|\broll|coin|random|chaos|d20|d6"),
    ("Transformation et doubles faces", r"shapesharing|type-cycling"
        r"|multi character|transform|dfc|flip|face|mutate|shapechange"
        r"|type change|animate|meld|day|night|werewolf|manifest|morph|disguise"),
    ("Valeur de mana et sorts X", r"mana value|\bmv\b|\bx\b|kicker|modal"
        r"|exponential|quadratic|mana cost"),
    ("Manipulation de la bibliothèque", r"soothsaying|bottom deck|explore|scry"
        r"|surveil|\btop\b|library|shuffle|reveal|look"),
    ("Conditions de victoire", r"combo|alternate loss|alternate win|\bwin\b"
        r"|lose the game"),
]

CATEGORIES = [name for name, _ in _RULES]
_COMPILED = [(name, re.compile(pattern)) for name, pattern in _RULES]


@lru_cache(maxsize=None)
def categorize(tag: str) -> str | None:
    """La catégorie d'un tag, ou None s'il ne dit rien du rôle de la carte."""
    lowered = (tag or "").strip().lower()
    if not lowered or _EXCLUDED.search(lowered):
        return None
    for name, pattern in _COMPILED:
        if pattern.search(lowered):
            return name
    return None


def card_categories(tags: list[str]) -> set[str]:
    """Les catégories que touche une carte, chacune une seule fois."""
    return {cat for cat in map(categorize, tags) if cat}


def summarize(
    deck: dict[str, int],
    population: dict[str, float],
    total_decks: int,
    tags: dict[str, list[str]],
    top: int = TOP,
) -> list[dict]:
    """Les catégories les plus jouées par les decks similaires, face au deck.

    `deck` et `population` associent un nom de carte à son nombre d'exemplaires
    — dans le deck, et cumulé sur les `total_decks` decks similaires. Le
    classement suit les decks similaires : c'est leur pratique qui dit ce qui
    compte pour ce commandant, le deck analysé s'y mesure.
    """
    if total_decks <= 0:
        return []

    def per_category(counts: dict[str, float]) -> dict[str, float]:
        totals: dict[str, float] = {}
        for name, qty in counts.items():
            for cat in card_categories(tags.get(name, [])):
                totals[cat] = totals.get(cat, 0.0) + qty
        return totals

    similar = per_category(population)
    mine = per_category(deck)
    ranked = sorted(similar.items(), key=lambda item: (-item[1], item[0]))[:top]

    # Le deck et les statistiques n'écrivent pas toujours un nom de la même
    # façon : casse, ou face avant seule d'une carte recto-verso.
    rate_by_key = {_key(name): count * 100 / total_decks
                   for name, count in population.items()}
    deck_keys = {_key(name) for name in deck}

    def in_category(names, cat):
        return [name for name in names if cat in card_categories(tags.get(name, []))]

    rows = []
    for cat, total in ranked:
        # Les cartes du deck, de la moins jouée ailleurs à la plus jouée : les
        # choix les plus personnels — ou les plus discutables — d'abord.
        cards = sorted(
            ({"card_name": name, "rate": round(rate_by_key.get(_key(name), 0.0), 1)}
             for name in in_category(deck, cat)),
            key=lambda card: (card["rate"], card["card_name"]))
        # Ce que les autres jouent le plus dans cette catégorie, et pas ce deck.
        missing = sorted(
            ({"card_name": name, "rate": round(rate_by_key[_key(name)], 1)}
             for name in in_category(population, cat)
             if _key(name) not in deck_keys),
            key=lambda card: (-card["rate"], card["card_name"]))[:MISSING]
        rows.append({
            "category": cat,
            "deck": int(mine.get(cat, 0)),
            "similar": round(total / total_decks, 1),
            "cards": cards,
            "missing": missing,
        })
    return rows


def _key(name: str) -> str:
    """Clé de rapprochement d'un nom : minuscules, face avant seule."""
    return (name or "").strip().lower().split(" // ")[0]


# Les noms sont d'abord réduits à leur liste distincte et normalisés une seule
# fois : normaliser des deux côtés d'une jointure la rend quadratique. Une carte
# recto-verso s'écrit tantôt « A // B », tantôt « A » seule : la face avant est
# rapprochée à part, sur la courte liste des cartes à deux faces.
_TAGS_SQL = """
WITH names AS (SELECT DISTINCT unnest(CAST(:names AS TEXT[])) AS n),
keyed AS (SELECT n, mm_normalize_name(n) AS k FROM names),
fronts AS (
    SELECT id, split_part(normalized_name, ' // ', 1) AS f
    FROM scryfall_cards
    WHERE normalized_name LIKE '%% // %%'
),
matched AS (
    SELECT k.n, c.id
    FROM keyed k JOIN scryfall_cards c ON c.normalized_name = k.k
    UNION
    SELECT k.n, f.id
    FROM keyed k JOIN fronts f ON f.f = k.k
)
SELECT DISTINCT m.n AS card_name, t.tag_name
FROM matched m
JOIN scryfall_card_tags t ON t.card_id = m.id
"""

# Les statistiques par commandant, et non les listes de `deck_cards` : le
# serveur ne reçoit que ces agrégats, `deck_cards` y est vide. Elles disent
# combien de decks jouent chaque carte, pas en combien d'exemplaires — d'où
# l'exclusion des terrains de base, seules cartes jouées par dizaines.
_POPULATION_SQL = """
SELECT card_name, decks_with_card, total_decks
FROM deck_stat_commander
WHERE commander = :cmd
  -- Le commandant figure parfois parmi les cartes de ses propres decks.
  AND lower(btrim(card_name)) <> ALL(CAST(:cmd_keys AS TEXT[]))
"""

_BASIC_LANDS = {
    name.lower()
    for base in ("Plains", "Island", "Swamp", "Mountain", "Forest")
    for name in (base, f"Snow-Covered {base}")
} | {"wastes", "snow-covered wastes"}


def is_basic_land(name: str) -> bool:
    """Terrain de base : écarté des deux côtés de la comparaison."""
    return (name or "").strip().lower() in _BASIC_LANDS


def card_tags(session: Session, names: list[str]) -> dict[str, list[str]]:
    """Les tags de chaque carte nommée ; une carte inconnue n'en a aucun."""
    if not names:
        return {}
    result: dict[str, list[str]] = {}
    for row in session.execute(text(_TAGS_SQL), {"names": sorted(set(names))}):
        result.setdefault(row.card_name, []).append(row.tag_name)
    return result


def population(session: Session, commander: str) -> tuple[dict[str, float], int]:
    """Les cartes des decks publics de ce commandant, et le nombre de ces decks."""
    params = {"cmd": commander, "cmd_keys": sorted(commander_keys(commander))}
    cards: dict[str, float] = {}
    total = 0
    for row in session.execute(text(_POPULATION_SQL), params):
        total = max(total, int(row.total_decks or 0))
        if not is_basic_land(row.card_name):
            cards[row.card_name] = float(row.decks_with_card or 0)
    return cards, total


def analyze(session: Session, commander: str, deck_cards: list[tuple[str, int]]) -> dict:
    """L'analyse poussée d'un deck : ses cartes par catégorie, face aux autres.

    `deck_cards` est la liste (nom, quantité) du deck ; les commandants et les
    terrains de base en sont écartés ici, comme ils le sont des decks similaires.
    """
    deck: dict[str, int] = {}
    for name, qty in deck_cards:
        if is_commander(commander, name) or is_basic_land(name):
            continue
        deck[name] = deck.get(name, 0) + int(qty or 0)

    pop, total = population(session, commander)
    tags = card_tags(session, list(deck) + list(pop))
    return {
        "decks": total,
        "deck_cards": sum(deck.values()),
        "categories": summarize(deck, pop, total, tags),
    }
