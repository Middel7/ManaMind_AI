"""
Parseurs XML : Magic Online .dek et Cockatrice .cod.
Protection XXE intégrée (pas d'entités externes, pas de DTD).
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from ..models import (
    CanonicalDeckImport,
    CanonicalEntry,
    ImportSource,
    ImportStatistics,
    Zone,
)
from .base import MAX_ENTRIES, BaseParser

_MAX_XML_BYTES = 2 * 1024 * 1024  # 2 Mo pour XML

# Bannir les entités externes dans le XML
_RE_DOCTYPE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE)
_RE_ENTITY = re.compile(r"<!ENTITY[^>]*>", re.IGNORECASE)

_COCKATRICE_ZONE_MAP = {
    "main": Zone.MAINBOARD,
    "side": Zone.SIDEBOARD,
    "sideboard": Zone.SIDEBOARD,
    "commander": Zone.COMMANDER,
    "maybeboard": Zone.MAYBEBOARD,
    "companion": Zone.COMPANION,
    "token": Zone.TOKEN,
    "tokens": Zone.TOKEN,
}


def _safe_parse_xml(text: str) -> ET.Element:
    """Parse XML avec protection XXE."""
    # Bloquer DOCTYPE et entités externes
    if _RE_DOCTYPE.search(text) or _RE_ENTITY.search(text):
        raise ValueError("XML with DOCTYPE/ENTITY declarations is not allowed (XXE protection)")
    if len(text.encode("utf-8")) > _MAX_XML_BYTES:
        raise ValueError(f"XML file too large (max {_MAX_XML_BYTES // 1024} KB)")
    return ET.fromstring(text)


class MtgoDekParser(BaseParser):
    """Parseur Magic Online .dek XML."""

    source_name = "mtgo"
    supported_extensions = [".dek"]
    supported_mime_types = ["application/xml", "text/xml"]

    def can_parse(self, raw: str | bytes) -> int:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)
        if "<Deck>" in raw and "CatID=" in raw:
            return 99
        return 0

    def parse(self, raw: str | bytes) -> CanonicalDeckImport:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)

        result = CanonicalDeckImport(
            source=ImportSource.MTGO,
            source_format="Magic Online .dek XML",
        )
        stats = ImportStatistics()

        try:
            root = _safe_parse_xml(raw)
        except (ET.ParseError, ValueError) as exc:
            result.errors.append(f"XML parse error: {exc}")
            return result

        lineno = 0
        for card_el in root.iter("Cards"):
            lineno += 1
            if len(result.entries) >= MAX_ENTRIES:
                result.errors.append("Too many entries, truncating")
                break

            name = card_el.get("Name", "").strip()
            if not name:
                continue

            try:
                qty = int(card_el.get("Quantity", "1"))
            except ValueError:
                qty = 1

            is_sb = card_el.get("Sideboard", "false").lower() == "true"
            zone = Zone.SIDEBOARD if is_sb else Zone.MAINBOARD
            cat_id = card_el.get("CatID")

            entry = CanonicalEntry(
                line_number=lineno,
                raw_line=f"{qty} {name}" + (" [SB]" if is_sb else ""),
                quantity=qty,
                raw_name=name,
                canonical_name=name,
                zone=zone,
                source_identifier=cat_id,
            )
            result.entries.append(entry)
            stats.cards_detected += 1
            stats.copies_detected += qty

        stats.lines_received = lineno
        result.statistics = stats
        result.detected_zones = list({e.zone.value for e in result.entries})
        return result


class CockatriceParser(BaseParser):
    """Parseur Cockatrice .cod XML."""

    source_name = "cockatrice"
    supported_extensions = [".cod"]
    supported_mime_types = ["application/xml", "text/xml"]

    def can_parse(self, raw: str | bytes) -> int:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)
        return 99 if "<cockatrice_deck" in raw.lower() else 0

    def parse(self, raw: str | bytes) -> CanonicalDeckImport:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)

        result = CanonicalDeckImport(
            source=ImportSource.COCKATRICE,
            source_format="Cockatrice .cod XML",
        )
        stats = ImportStatistics()

        try:
            root = _safe_parse_xml(raw)
        except (ET.ParseError, ValueError) as exc:
            result.errors.append(f"XML parse error: {exc}")
            return result

        # Nom du deck
        deck_name_el = root.find("deckname")
        if deck_name_el is not None and deck_name_el.text:
            result.deck_name = deck_name_el.text.strip()

        # Format du deck
        general_el = root.find("general")
        if general_el is not None:
            format_el = general_el.find("format")
            if format_el is not None and format_el.text:
                result.format = format_el.text.strip().lower()

        detected_zones: set[str] = set()
        lineno = 0

        for zone_el in root.findall("zone"):
            zone_name = zone_el.get("name", "").lower()
            zone = _COCKATRICE_ZONE_MAP.get(zone_name, Zone.OTHER)

            for card_el in zone_el.findall("card"):
                lineno += 1
                if len(result.entries) >= MAX_ENTRIES:
                    result.errors.append("Too many entries, truncating")
                    break

                name = card_el.get("name", "").strip()
                if not name:
                    continue

                try:
                    qty = int(card_el.get("number", "1"))
                except ValueError:
                    qty = 1

                detected_zones.add(zone.value)
                entry = CanonicalEntry(
                    line_number=lineno,
                    raw_line=f"{qty} {name}",
                    quantity=qty,
                    raw_name=name,
                    canonical_name=name,
                    zone=zone,
                )
                result.entries.append(entry)
                stats.cards_detected += 1
                stats.copies_detected += qty

        if Zone.COMMANDER.value in detected_zones:
            result.format = result.format or "commander"

        stats.lines_received = lineno
        result.statistics = stats
        result.detected_zones = sorted(detected_zones)
        return result
