"""
Parseur texte générique — couvre tous les formats texte :
Arena, Moxfield export, MTGO texte, format simple, sections structurées.
"""

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

# En-têtes de métadonnées Moxfield/Arena à ignorer silencieusement
_RE_META_NAME = re.compile(r'^Name\s+"?([^"]+)"?\s*$', re.IGNORECASE)
_META_SKIP_LINES = re.compile(
    r'^(About|Description|Format|Deck\s+Description)\s*$',
    re.IGNORECASE,
)


class TextParser(BaseParser):
    source_name = "generic_text"
    supported_extensions = [".txt", ".dck", ".dek"]
    supported_mime_types = ["text/plain"]

    def can_parse(self, raw: str | bytes) -> int:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        if not lines:
            return 0
        card_lines = sum(1 for ln in lines if self._is_card_line(ln))
        if card_lines == 0:
            return 0
        ratio = card_lines / len(lines)
        return min(70, int(ratio * 100))

    def parse(self, raw: str | bytes) -> CanonicalDeckImport:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)

        # Normalise les fins de ligne
        raw = raw.replace("\r\n", "\n").replace("\r", "\n")

        result = CanonicalDeckImport(
            source=ImportSource.GENERIC_TEXT,
            source_format="Generic text",
        )

        lines = raw.splitlines()
        if len(lines) > MAX_LINES:
            result.errors.append(f"File too large: {len(lines)} lines (max {MAX_LINES})")
            lines = lines[:MAX_LINES]

        current_zone = Zone.MAINBOARD
        deck_name: str | None = None
        detected_zones: set[str] = set()
        stats = ImportStatistics(lines_received=len(lines))

        # Détection du nom de deck depuis un commentaire initial
        for line in lines[:5]:
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("#"):
                candidate = stripped.lstrip("/#").strip()
                if candidate and not self._is_card_line(stripped):
                    deck_name = candidate
                    break

        result.deck_name = deck_name

        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()

            if not stripped or stripped.startswith("//") or stripped.startswith("#"):
                continue

            # Ligne de métadonnée "Name ..." → nom du deck
            m_name = _RE_META_NAME.match(stripped)
            if m_name:
                if not result.deck_name:
                    result.deck_name = m_name.group(1).strip()
                continue

            # En-têtes de section non-carte à ignorer (About, Description…)
            if _META_SKIP_LINES.match(stripped):
                continue

            # Zone header ?
            zone = self.detect_zone_header(stripped)
            if zone is not None:
                current_zone = zone
                detected_zones.add(zone.value)
                continue

            # Ligne carte
            parsed = self.parse_card_line(stripped, lineno, current_zone)
            if parsed is None:
                result.warnings.append(f"Line {lineno} ignored: {line.rstrip()!r}")
                stats.ignored_entries += 1
                continue

            if len(result.entries) >= MAX_ENTRIES:
                result.errors.append(f"Too many entries (max {MAX_ENTRIES}), truncating")
                break

            # Si on est en zone MAINBOARD et qu'un marqueur CMDR est présent → override
            zone_final = parsed["zone"]
            detected_zones.add(zone_final.value)

            entry = CanonicalEntry(
                line_number=parsed["line_number"],
                raw_line=parsed["raw_line"],
                quantity=parsed["quantity"],
                raw_name=parsed["raw_name"],
                canonical_name=parsed["raw_name"],
                set_code=parsed["set_code"],
                collector_number=parsed["collector_number"],
                finish=parsed["finish"],
                zone=zone_final,
            )

            result.entries.append(entry)
            stats.cards_detected += 1
            stats.copies_detected += entry.quantity

        # Détection format Commander si une zone commander existe
        if Zone.COMMANDER.value in detected_zones:
            result.format = "commander"

        result.detected_zones = sorted(detected_zones)
        result.statistics = stats

        # Recalcul des zones détectées dans les stats finales
        result.source_format = self._guess_source_format(raw, result)

        return result

    def _is_card_line(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        parsed = self.parse_card_line(stripped, 0)
        return parsed is not None and bool(parsed["raw_name"])

    def _guess_source_format(self, raw: str, result: CanonicalDeckImport) -> str:
        """Affine la détection de source entre Arena, MTGO, Moxfield, générique."""
        import re

        # Arena : en-têtes + lignes "(SET) num"
        has_arena_headers = bool(re.search(
            r"^(Commander|Deck|Sideboard)\s*$", raw, re.MULTILINE | re.IGNORECASE
        ))
        has_set_num = any(
            e.set_code and e.collector_number for e in result.entries
        )
        if has_arena_headers and has_set_num:
            result.source = ImportSource.ARENA
            return "MTG Arena"

        # MTGO SB:
        if re.search(r"^SB:\s+", raw, re.MULTILINE):
            result.source = ImportSource.MTGO
            return "Magic Online text"

        if has_set_num:
            result.source = ImportSource.MOXFIELD
            return "Moxfield / structured text"

        return "Generic text"
