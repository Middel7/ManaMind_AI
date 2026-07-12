"""Parseur XMage .dck — lignes du type '1 [SET:num] Card Name'."""

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

_RE_LINE = re.compile(
    r"""
    ^\s*
    (?P<qty>\d{1,4})\s+
    \[(?P<set>[A-Za-z0-9]+):(?P<num>[A-Za-z0-9★\-]+)\]\s+
    (?P<name>.+?)\s*$
    """,
    re.VERBOSE | re.MULTILINE,
)
_RE_SB = re.compile(r"^SB:\s+", re.IGNORECASE)


class XMageParser(BaseParser):
    source_name = "xmage"
    supported_extensions = [".dck"]
    supported_mime_types = ["text/plain"]

    def can_parse(self, raw: str | bytes) -> int:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)
        # Ne pas confondre avec Forge (qui commence par [metadata])
        if re.search(r"^\[metadata\]", raw, re.MULTILINE | re.IGNORECASE):
            return 0
        matches = _RE_LINE.findall(raw) if not isinstance(raw, bytes) else []
        if not matches:
            # Essai sans conversion
            matches = _RE_LINE.findall(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
        return min(95, 50 + len(matches) * 10) if matches else 0

    def parse(self, raw: str | bytes) -> CanonicalDeckImport:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)
        raw = raw.replace("\r\n", "\n").replace("\r", "\n")

        result = CanonicalDeckImport(
            source=ImportSource.XMAGE,
            source_format="XMage .dck",
        )
        lines = raw.splitlines()
        if len(lines) > MAX_LINES:
            result.errors.append(f"Too many lines: {len(lines)}")
            lines = lines[:MAX_LINES]

        stats = ImportStatistics(lines_received=len(lines))
        detected_zones: set[str] = set()
        current_zone = Zone.MAINBOARD

        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue

            # Zone header ?
            zone = self.detect_zone_header(stripped)
            if zone is not None:
                current_zone = zone
                detected_zones.add(zone.value)
                continue

            is_sb = bool(_RE_SB.match(stripped))
            line_clean = _RE_SB.sub("", stripped) if is_sb else stripped
            effective_zone = Zone.SIDEBOARD if is_sb else current_zone

            m = _RE_LINE.match(line_clean)
            if not m:
                result.warnings.append(f"Line {lineno} not recognized: {stripped!r}")
                stats.ignored_entries += 1
                continue

            if len(result.entries) >= MAX_ENTRIES:
                result.errors.append("Too many entries, truncating")
                break

            zone_final = effective_zone
            detected_zones.add(zone_final.value)

            entry = CanonicalEntry(
                line_number=lineno,
                raw_line=line.rstrip(),
                quantity=int(m.group("qty")),
                raw_name=m.group("name").strip(),
                canonical_name=m.group("name").strip(),
                set_code=m.group("set").upper(),
                collector_number=m.group("num"),
                zone=zone_final,
            )
            result.entries.append(entry)
            stats.cards_detected += 1
            stats.copies_detected += entry.quantity

        if Zone.COMMANDER.value in detected_zones:
            result.format = "commander"

        result.detected_zones = sorted(detected_zones)
        result.statistics = stats
        return result
