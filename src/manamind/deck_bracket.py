"""Bracket Commander d'un deck : plancher calculé, et choix du joueur.

Le système officiel range un deck de 1 (Exhibition) à 5 (cEDH). Ses règles
fixent un plancher — une carte interdite en dessous d'un niveau y fait monter
le deck — mais pas le niveau lui-même, qui dépend aussi de l'intention du
joueur. Le calcul ne donne donc que ce plancher, en partant du bracket 2 : le
1 et le 5 ne se déduisent pas d'une liste, seul le joueur peut les choisir.

Trois critères sont tenus :
- les Game Changers (indicateur Scryfall `game_changer`) : 1 à 3 → 3, au-delà → 4 ;
- la destruction massive de terrains : une seule carte → 4 ;
- les tours supplémentaires : à partir de trois cartes, on les tient pour
  enchaînables → 4.

Les combos infinies à deux cartes ne sont pas évaluées : aucune base de combos
n'est disponible. Le choix manuel couvre ce cas.
"""

from __future__ import annotations

from sqlalchemy import text

BRACKETS = (1, 2, 3, 4, 5)

# Au-delà de ce nombre de Game Changers, le bracket 3 ne suffit plus.
MAX_GAME_CHANGERS_B3 = 3
# Nombre de cartes de tours supplémentaires à partir duquel on les suppose
# enchaînées : la liste seule ne dit pas si elles se rejouent.
EXTRA_TURNS_B4 = 3

# Destruction massive de terrains, au sens du système de brackets : détruire,
# exiler ou renvoyer la plupart des terrains, ou empêcher durablement qu'ils se
# dégagent ou produisent le mana voulu. L'étiquette Scryfall « mass land
# denial » ne sert pas : elle n'est posée que sur une partie du catalogue, et
# range parmi ces cartes Liliana of the Veil ou Harbinger of the Seas.
MASS_LAND_DENIAL = frozenset(name.lower() for name in (
    "Acid Rain", "Apocalypse", "Armageddon", "Back to Basics",
    "Bearer of the Heavens", "Blood Moon", "Boil", "Boiling Seas",
    "Burning Sands", "Catastrophe", "Choke", "Contamination", "Death Cloud",
    "Decree of Annihilation", "Desolation Angel", "Destructive Flow",
    "Devastating Dreams", "Devastation", "Epicenter", "Fall of the Thran",
    "Flashfires", "Global Ruin", "Hokori, Dust Drinker", "Impending Disaster",
    "Infernal Darkness", "Jokulhaups", "Keldon Firebombers", "Magus of the Moon",
    "Mana Vortex", "Myojin of Infinite Rage", "Obliterate", "Ravages of War",
    "Razia's Purification", "Realm Razer", "Rising Waters", "Ritual of Subdual",
    "Ruination", "Static Orb", "Stasis", "Sunder", "Tectonic Break",
    "Thoughts of Ruin", "Tsunami", "Wildfire", "Winter Moon", "Winter Orb",
    "Worldfire", "Worldpurge", "Worldslayer",
))

# Repère les cartes qui donnent un tour supplémentaire d'après leur texte,
# disponible pour tout le catalogue. « Players can't take extra turns »
# (Stranglehold) n'y répond pas : il y faut un article avant « extra ».
_EXTRA_TURN_RE = r"takes? (an|one|two|three|x|that many) extra turns?"


def computed_bracket(game_changers: int, mass_land_denial: int,
                     extra_turns: int) -> int:
    """Plancher de bracket d'après les comptes de cartes sensibles."""
    if (game_changers > MAX_GAME_CHANGERS_B3 or mass_land_denial
            or extra_turns >= EXTRA_TURNS_B4):
        return 4
    if game_changers:
        return 3
    return 2


def deck_flags(session, user_id: int, deck_id: str | None = None) -> dict[str, dict]:
    """Cartes sensibles de chaque deck de l'utilisateur, ou d'un seul.

    Renvoie, par deck, les noms de ses Game Changers, de ses cartes de
    destruction massive de terrains et de ses tours supplémentaires. Un deck
    sans aucune de ces cartes est absent du résultat.
    """
    # Les noms du deck sont normalisés une seule fois dans le CTE : la jointure
    # sur la colonne normalized_name reste alors un hash join.
    rows = session.execute(text(f"""
        WITH names AS (
            SELECT DISTINCT dc.deck_id, dc.card_name,
                   mm_normalize_name(dc.card_name) AS norm
            FROM user_deck_cards dc
            WHERE dc.user_id = :uid
              {"AND dc.deck_id = :did" if deck_id is not None else ""}
        )
        SELECT n.deck_id, n.card_name,
               bool_or(COALESCE(c.game_changer, false)) AS game_changer,
               bool_or(COALESCE(c.oracle_text, '') ~* :extra) AS extra_turn,
               lower(split_part(n.card_name, ' // ', 1)) = ANY(:mld) AS land_denial
        FROM names n
        JOIN scryfall_cards c ON c.normalized_name = n.norm
        GROUP BY n.deck_id, n.card_name
        HAVING bool_or(COALESCE(c.game_changer, false))
            OR bool_or(COALESCE(c.oracle_text, '') ~* :extra)
            OR lower(split_part(n.card_name, ' // ', 1)) = ANY(:mld)
        ORDER BY n.card_name
    """), {"uid": user_id, "did": deck_id, "extra": _EXTRA_TURN_RE,
           "mld": sorted(MASS_LAND_DENIAL)}).fetchall()

    flags: dict[str, dict] = {}
    for row in rows:
        entry = flags.setdefault(row.deck_id, {
            "game_changers": [], "mass_land_denial": [], "extra_turns": []})
        if row.game_changer:
            entry["game_changers"].append(row.card_name)
        if row.land_denial:
            entry["mass_land_denial"].append(row.card_name)
        if row.extra_turn:
            entry["extra_turns"].append(row.card_name)
    return flags


def manual_brackets(session, user_id: int) -> dict[str, int]:
    """Brackets choisis à la main par l'utilisateur, par deck."""
    rows = session.execute(text("""
        SELECT deck_id, bracket FROM user_deck_bracket WHERE user_id = :uid
    """), {"uid": user_id}).fetchall()
    return {row.deck_id: int(row.bracket) for row in rows}


def bracket_summary(flags: dict | None, manual: int | None, *,
                    detail: bool = False) -> dict:
    """Bracket retenu pour un deck : le choix du joueur, sinon le calcul."""
    flags = flags or {"game_changers": [], "mass_land_denial": [], "extra_turns": []}
    computed = computed_bracket(len(flags["game_changers"]),
                                len(flags["mass_land_denial"]),
                                len(flags["extra_turns"]))
    summary = {
        "value": manual or computed,
        "computed": computed,
        "manual": manual is not None,
    }
    if detail:
        summary.update(flags)
    return summary
