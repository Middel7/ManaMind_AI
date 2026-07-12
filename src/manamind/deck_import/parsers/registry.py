"""
Registre central des parseurs.
Sélectionne le meilleur parseur par score can_parse() ou via le détecteur.
"""

from __future__ import annotations

from ..models import CanonicalDeckImport
from .base import MAX_FILE_BYTES, BaseParser
from .csv_parser import CsvParser
from .forge_parser import ForgeParser
from .json_parser import JsonParser
from .text_parser import TextParser
from .xmage_parser import XMageParser
from .xml_parser import CockatriceParser, MtgoDekParser

# Ordre de priorité (en cas d'égalité, le premier de la liste gagne)
_PARSERS: list[BaseParser] = [
    CockatriceParser(),
    MtgoDekParser(),
    ForgeParser(),
    XMageParser(),
    JsonParser(),
    CsvParser(),
    TextParser(),  # Fallback générique en dernier
]


def select_parser(raw: str | bytes) -> tuple[BaseParser, int]:
    """
    Sélectionne le parseur le plus confiant.
    Retourne (parseur, score).
    """
    best_parser = _PARSERS[-1]  # TextParser par défaut
    best_score = 0

    for parser in _PARSERS:
        try:
            score = parser.can_parse(raw)
        except Exception:
            score = 0
        if score > best_score:
            best_score = score
            best_parser = parser

    return best_parser, best_score


def parse(raw: str | bytes, source_hint: str | None = None) -> CanonicalDeckImport:
    """
    Point d'entrée principal du pipeline de parsing.
    Détecte le format, sélectionne le parseur, parse et retourne le modèle canonique.
    """
    # Vérification taille
    size = len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8", errors="replace"))
    if size > MAX_FILE_BYTES:
        result = CanonicalDeckImport()
        result.errors.append(f"Content too large: {size} bytes (max {MAX_FILE_BYTES})")
        return result

    if not raw or (isinstance(raw, (str, bytes)) and not raw.strip()):
        result = CanonicalDeckImport()
        result.errors.append("Empty content")
        return result

    parser, score = select_parser(raw)
    result = parser.parse(raw)
    result.statistics.lines_received = result.statistics.lines_received or (
        len(raw.splitlines()) if isinstance(raw, str) else len(raw.decode("utf-8", "replace").splitlines())
    )
    return result


def get_all_parsers() -> list[BaseParser]:
    return list(_PARSERS)
