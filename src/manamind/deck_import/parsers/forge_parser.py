"""Parseur Forge .dck — fichier INI-like avec sections [metadata], [main], etc."""

from __future__ import annotations

import re

from ..models import (
    CanonicalDeckImport,
    CanonicalEntry,
    ImportSource,
    ImportStatistics,
    Zone,
)
from .base import MAX_ENTRIES, MAX_LINES, BaseParser

_RE_SECTION = re.compile(r"^\[([^\]]+)\]$")
_RE_CARD = re.compile(
    r"""
    ^\s*
    (?P<qty>\d{1,4})\s+
    (?P<name>[^|]+?)
    (?:\|(?P<set>[A-Za-z0-9]+))?
    (?:\|(?P<num>[A-Za-z0-9★\-]+))?
    \s*$
    """,
    re.VERBOSE,
)

_SECTION_ZONES: dict[str, Zone] = {
    "commander": Zone.COMMANDER,
    "main": Zone.MAINBOARD,
    "mainboard": Zone.MAINBOARD,
    "sideboard": Zone.SIDEBOARD,
    "side": Zone.SIDEBOARD,
    "maybeboard": Zone.MAYBEBOARD,
    "maybe": Zone.MAYBEBOARD,
    "companion": Zone.COMPANION,
    "token": Zone.TOKEN,
    "tokens": Zone.TOKEN,
}


class ForgeParser(BaseParser):
    source_name = "forge"
    supported_extensions = [".dck"]
    supported_mime_types = ["text/plain"]

    def can_parse(self, raw: str | bytes) -> int:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)
        has_metadata = bool(re.search(r"^\[metadata\]", raw, re.MULTILINE | re.IGNORECASE))
        has_main = bool(re.search(r"^\[main\]", raw, re.MULTILINE | re.IGNORECASE))
        if has_metadata and has_main:
            return 98
        if has_metadata:
            return 60
        return 0

    def parse(self, raw: str | bytes) -> CanonicalDeckImport:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)
        raw = raw.replace("\r\n", "\n").replace("\r", "\n")

        result = CanonicalDeckImport(
            source=ImportSource.FORGE,
            source_format="Forge .dck",
        )
        lines = raw.splitlines()
        if len(lines) > MAX_LINES:
            result.errors.append(f"Too many lines: {len(lines)}")
            lines = lines[:MAX_LINES]

        stats = ImportStatistics(lines_received=len(lines))
        detected_zones: set[str] = set()
        current_zone: Zone | None = None

        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue

            # Métadonnées ?
            sec = _RE_SECTION.match(stripped)
            if sec:
                sec_name = sec.group(1).lower()
                if sec_name == "metadata":
                    current_zone = None
                else:
                    current_zone = _SECTION_ZONES.get(sec_name, Zone.OTHER)
                    if current_zone != Zone.OTHER:
                        detected_zones.add(current_zone.value)
                continue

            # Métadonnée Name= ?
            if current_zone is None:
                if stripped.lower().startswith("name="):
                    result.deck_name = stripped[5:].strip()
                elif stripped.lower().startswith("format="):
                    result.format = stripped[7:].strip().lower()
                continue

            if current_zone == Zone.TOKEN:
                continue

            m = _RE_CARD.match(stripped)
            if not m:
                result.warnings.append(f"Line {lineno} not recognized: {stripped!r}")
                stats.ignored_entries += 1
                continue

            if len(result.entries) >= MAX_ENTRIES:
                result.errors.append("Too many entries, truncating")
                break

            detected_zones.add(current_zone.value)
            entry = CanonicalEntry(
                line_number=lineno,
                raw_line=line.rstrip(),
                quantity=int(m.group("qty")),
                raw_name=m.group("name").strip(),
                canonical_name=m.group("name").strip(),
                set_code=(m.group("set") or "").upper() or None,
                collector_number=m.group("num") or None,
                zone=current_zone,
            )
            result.entries.append(entry)
            stats.cards_detected += 1
            stats.copies_detected += entry.quantity

        if Zone.COMMANDER.value in detected_zones:
            result.format = result.format or "commander"

        result.detected_zones = sorted(detected_zones)
        result.statistics = stats
        return result
