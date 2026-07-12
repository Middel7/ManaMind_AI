"""Interface commune pour tous les parseurs de decklists."""

from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod

from ..models import CanonicalDeckImport, Zone

# ── Constantes globales ──────────────────────────────────────────────────────

MAX_FILE_BYTES = 5 * 1024 * 1024   # 5 Mo
MAX_LINES = 5_000
MAX_QUANTITY = 1_000
MAX_ENTRIES = 2_000

# Marqueurs de commandant reconnus
_CMDR_MARKERS = frozenset({
    "*cmdr*", "cmdr", "*commander*", "commander", "*commandant*", "commandant",
    "*cmpn*", "cmpn", "*companion*", "companion", "*partner*", "partner",
    "*background*", "background",
})

# Marqueurs companion distincts
_COMPANION_MARKERS = frozenset({"*cmpn*", "cmpn", "*companion*", "companion"})

# Regex de lignes zone-header
_ZONE_HEADERS: list[tuple[re.Pattern, Zone]] = [
    (re.compile(
        r"^(?:/{2}\s*|#\s*|\[\s*)?(?:commander|commanders?|command\s+zone|commandant)\s*:?\s*\]?\s*$",
        re.IGNORECASE
    ), Zone.COMMANDER),
    (re.compile(
        r"^(?:/{2}\s*|#\s*|\[\s*)?(?:deck|main(?:board)?|main\s*deck|maindeck)\s*:?\s*\]?\s*$",
        re.IGNORECASE
    ), Zone.MAINBOARD),
    (re.compile(
        r"^(?:/{2}\s*|#\s*|\[\s*)?(?:sideboard|side\s*board|side|sb|r[eé]serve)\s*:?\s*\]?\s*$",
        re.IGNORECASE
    ), Zone.SIDEBOARD),
    (re.compile(
        r"^(?:/{2}\s*|#\s*|\[\s*)?(?:maybeboard|maybe|considering|ideas)\s*:?\s*\]?\s*$",
        re.IGNORECASE
    ), Zone.MAYBEBOARD),
    (re.compile(
        r"^(?:/{2}\s*|#\s*|\[\s*)?(?:companion)\s*:?\s*\]?\s*$",
        re.IGNORECASE
    ), Zone.COMPANION),
    (re.compile(
        r"^(?:/{2}\s*|#\s*|\[\s*)?(?:tokens?|emblems?|attractions?|stickers?|lessons?)\s*:?\s*\]?\s*$",
        re.IGNORECASE
    ), Zone.TOKEN),
]

# Regex principale de ligne carte (texte générique)
# Captures: qty_pre, name_raw, set_code, col_num, qty_suf, cmdr_marker
_RE_CARD = re.compile(
    r"""
    ^\s*
    # quantité optionnelle avant le nom
    (?:(?P<qty_pre>\d{1,4})[xX]?\s+)?
    # nom de la carte (capture greedy jusqu'à la fin ou début d'annotation)
    (?P<name>[^\[\]()\r\n]+?)
    # édition optionnelle entre () ou []
    (?:
        \s*[\(\[]\s*(?P<set>[A-Za-z0-9]{2,8})\s*[:\-]?\s*(?P<num1>[A-Za-z0-9★\-]+)?\s*[\)\]]
        |
        \s*[\(\[]\s*(?P<set2>[A-Za-z0-9]{2,8})\s*[\)\]]
    )?
    # numéro de collection hors parenthèses (doit commencer par un chiffre ou ★)
    (?:\s+(?P<num2>[\d★][A-Za-z0-9★\-]*))?
    # marqueurs commander/companion
    (?:\s+(?P<marker>\*?(?:CMDR|COMMANDER|COMMANDANT|CMPN|COMPANION|PARTNER|BACKGROUND)\*?))?
    # quantité après le nom (format "Sol Ring x4" ou "Sol Ring (4)")
    (?:\s+[xX](?P<qty_suf>\d{1,4}))?
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)

# SB: préfixe (MTGO / XMage)
_RE_SB_PREFIX = re.compile(r"^SB:\s+", re.IGNORECASE)

# Foil annotation *F* ou [FOIL] etc.
_RE_FOIL = re.compile(r"\*[Ff]\*|\[foil\]", re.IGNORECASE)
_RE_ETCHED = re.compile(r"\*[Ee]\*|\[etched\]", re.IGNORECASE)


class BaseParser(ABC):
    """Interface commune pour tous les parseurs."""

    source_name: str = "unknown"
    supported_extensions: list[str] = []
    supported_mime_types: list[str] = []

    @abstractmethod
    def can_parse(self, raw: str | bytes) -> int:
        """Retourne un score 0-100 indiquant la confiance de parsing."""
        ...

    @abstractmethod
    def parse(self, raw: str | bytes) -> CanonicalDeckImport:
        """Parse le contenu brut et retourne un modèle canonique."""
        ...

    # ── Utilitaires partagés ─────────────────────────────────────────────────

    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalise un nom de carte : NFD, suppress diacritiques, strip."""
        nfd = unicodedata.normalize("NFD", name)
        return "".join(c for c in nfd if unicodedata.category(c) != "Mn").strip()

    @staticmethod
    def decode_bytes(raw: bytes) -> str:
        if raw.startswith(b"\xef\xbb\xbf"):
            return raw[3:].decode("utf-8", errors="replace")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp1252", errors="replace")

    @staticmethod
    def detect_zone_header(line: str) -> Zone | None:
        stripped = line.strip()
        for pattern, zone in _ZONE_HEADERS:
            if pattern.match(stripped):
                return zone
        return None

    @staticmethod
    def parse_card_line(
        line: str,
        line_number: int,
        default_zone: Zone = Zone.MAINBOARD,
    ) -> dict | None:
        """
        Parse une ligne de carte au format texte générique.
        Retourne un dict avec les champs extraits, ou None si la ligne n'est pas une carte.
        """
        original = line
        # SB: préfixe
        is_sb = bool(_RE_SB_PREFIX.match(line))
        if is_sb:
            line = _RE_SB_PREFIX.sub("", line)
            default_zone = Zone.SIDEBOARD

        # Annotations foil / etched
        finish = None
        if _RE_ETCHED.search(line):
            finish = "etched"
        elif _RE_FOIL.search(line):
            finish = "foil"
        line = _RE_FOIL.sub("", _RE_ETCHED.sub("", line)).strip()

        m = _RE_CARD.match(line.strip())
        if not m:
            return None

        qty_pre = m.group("qty_pre")
        qty_suf = m.group("qty_suf")
        quantity = int(qty_pre or qty_suf or 1)
        if quantity > MAX_QUANTITY:
            quantity = MAX_QUANTITY

        raw_name = m.group("name").strip()
        # Retire les annotations résiduelles en fin de nom (ex: " *CMDR*")
        raw_name = re.sub(r"\s+\*?\w+\*?\s*$", lambda mm: "" if mm.group().strip().lower().strip("*") in {
            "cmdr", "commander", "commandant", "cmpn", "companion", "partner", "background", "f", "e"
        } else mm.group(), raw_name).strip()
        # Retire les tirets trailing (format "Nom - SET - num")
        raw_name = raw_name.rstrip(" -").strip()

        if not raw_name:
            return None

        set_code = (m.group("set") or m.group("set2") or "").upper() or None
        col_num = (m.group("num1") or m.group("num2") or "").strip() or None
        # Refuse un col_num qui n'est pas plausible (ex: un second mot du nom)
        if col_num and not re.match(r"^[0-9★][A-Za-z0-9★\-]*$", col_num):
            col_num = None

        marker_raw = (m.group("marker") or "").lower().strip("*")
        zone = default_zone
        if marker_raw in _COMPANION_MARKERS:
            zone = Zone.COMPANION
        elif marker_raw in _CMDR_MARKERS:
            zone = Zone.COMMANDER

        return {
            "line_number": line_number,
            "raw_line": original.rstrip(),
            "quantity": quantity,
            "raw_name": raw_name,
            "set_code": set_code,
            "collector_number": col_num,
            "finish": finish,
            "zone": zone,
            "is_sb_prefixed": is_sb,
        }
