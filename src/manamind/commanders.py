"""Format canonique du ou des commandants d'un deck.

Un deck Commander peut en avoir deux : Partner, Background, Doctor's
companion. Les deux tiennent dans une seule chaine, « A & B », les noms
ranges par ordre alphabetique pour que la meme paire s'ecrive toujours de la
meme facon quel que soit le deckbuilder d'origine. C'est sous cette forme que
les tables de reference (deck_stat_commander, commander_clusters, deck_cards)
indexent les decks publics : une paire mal ordonnee, ou reduite a un seul de
ses deux noms, n'y trouve aucune statistique et rend toute analyse muette.

« // » ne separe pas deux commandants : ce sont les deux faces d'une meme
carte recto-verso, qui ne compte que pour un.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

SEPARATOR = " & "

# Les regles du format n'autorisent pas un troisieme commandant.
MAX_COMMANDERS = 2

# « + » a longtemps servi de separateur cote import Moxfield : il est encore
# accepte en lecture, mais plus jamais ecrit. Aucun nom de carte ne le porte —
# les cartes coupees s'ecrivent « Fire // Ice ».
_SPLIT = re.compile(r"\s*[&+]\s*")


def split_commanders(raw: str | None) -> list[str]:
    """Les commandants portes par une chaine, dans leur ordre d'ecriture."""
    if not raw:
        return []
    return [part.strip() for part in _SPLIT.split(raw) if part.strip()]


def join_commanders(names: Iterable[str]) -> str:
    """Ecrit des commandants sous leur forme canonique.

    Les doublons sont ecartes, la casse d'origine conservee. Un nom qui porte
    lui-meme un « & » (« Leo, Chaos & Order ») rendrait la chaine ambigue au
    redecoupage : la paire garde alors l'ordre donne plutot que d'etre triee,
    faute de mieux — mieux vaut un ordre imprevisible qu'un commandant perdu.
    """
    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        cleaned = (name or "").strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            unique.append(cleaned)

    if len(unique) > 1 and any("&" in name for name in unique):
        return SEPARATOR.join(unique)
    return SEPARATOR.join(sorted(unique, key=str.lower))


def is_commander(raw: str | None, card_name: str) -> bool:
    """Cette carte est-elle l'un des commandants du deck ?"""
    target = (card_name or "").strip().lower()
    if not target:
        return False
    return any(name.lower() == target for name in split_commanders(raw))
