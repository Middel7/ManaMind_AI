"""Modèle canonique partagé par tous les parseurs d'import de decklists."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Zone(str, Enum):
    COMMANDER = "commander"
    MAINBOARD = "mainboard"
    SIDEBOARD = "sideboard"
    MAYBEBOARD = "maybeboard"
    COMPANION = "companion"
    TOKEN = "token"
    OTHER = "other"


class ResolutionStatus(str, Enum):
    EXACT_IDENTIFIER = "exact_identifier"
    EXACT_PRINTING = "exact_printing"
    EXACT_CARD_UNKNOWN_PRINTING = "exact_card_unknown_printing"
    PROBABLE_MATCH = "probable_match"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    UNSUPPORTED_DIGITAL_CARD = "unsupported_digital_card"
    IGNORED_ZONE = "ignored_zone"
    INVALID_LINE = "invalid_line"


class ImportSource(str, Enum):
    MOXFIELD = "moxfield"
    ARCHIDEKT = "archidekt"
    MANABOX = "manabox"
    ARENA = "arena"
    MTGO = "mtgo"
    COCKATRICE = "cockatrice"
    XMAGE = "xmage"
    FORGE = "forge"
    GENERIC_TEXT = "generic_text"
    CSV = "csv"
    JSON = "json"
    URL = "url"
    UNKNOWN = "unknown"


@dataclass
class CanonicalEntry:
    line_number: int
    raw_line: str
    quantity: int = 1
    raw_name: str = ""
    canonical_name: str = ""
    set_code: str | None = None
    collector_number: str | None = None
    language: str | None = None
    condition: str | None = None
    finish: str | None = None  # "nonfoil" | "foil" | "etched"
    zone: Zone = Zone.MAINBOARD
    tags: list[str] = field(default_factory=list)
    source_identifier: str | None = None
    scryfall_id: str | None = None
    oracle_id: str | None = None
    cardmarket_product_id: int | None = None
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    confidence: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "line_number": self.line_number,
            "raw_line": self.raw_line,
            "quantity": self.quantity,
            "raw_name": self.raw_name,
            "canonical_name": self.canonical_name,
            "set_code": self.set_code,
            "collector_number": self.collector_number,
            "language": self.language,
            "condition": self.condition,
            "finish": self.finish,
            "zone": self.zone.value,
            "tags": self.tags,
            "source_identifier": self.source_identifier,
            "scryfall_id": self.scryfall_id,
            "oracle_id": self.oracle_id,
            "cardmarket_product_id": self.cardmarket_product_id,
            "resolution_status": self.resolution_status.value,
            "confidence": self.confidence,
            "warnings": self.warnings,
        }


@dataclass
class ImportStatistics:
    lines_received: int = 0
    cards_detected: int = 0
    copies_detected: int = 0
    exact_matches: int = 0
    ambiguous_matches: int = 0
    unresolved_entries: int = 0
    ignored_entries: int = 0

    def to_dict(self) -> dict:
        return {
            "lines_received": self.lines_received,
            "cards_detected": self.cards_detected,
            "copies_detected": self.copies_detected,
            "exact_matches": self.exact_matches,
            "ambiguous_matches": self.ambiguous_matches,
            "unresolved_entries": self.unresolved_entries,
            "ignored_entries": self.ignored_entries,
        }


@dataclass
class CanonicalDeckImport:
    source: ImportSource = ImportSource.UNKNOWN
    source_format: str = "unknown"
    deck_name: str | None = None
    format: str | None = None  # commander | standard | modern | legacy | unknown
    entries: list[CanonicalEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    statistics: ImportStatistics = field(default_factory=ImportStatistics)
    # Zones détectées dans le fichier source
    detected_zones: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source.value,
            "source_format": self.source_format,
            "deck_name": self.deck_name,
            "format": self.format,
            "entries": [e.to_dict() for e in self.entries],
            "warnings": self.warnings,
            "errors": self.errors,
            "statistics": self.statistics.to_dict(),
            "detected_zones": self.detected_zones,
        }


@dataclass
class DetectionResult:
    source: ImportSource
    source_format: str
    confidence: int  # 0-100
    alternatives: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
