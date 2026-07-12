"""
Parseur CSV / TSV — détection automatique du séparateur et des colonnes.
Supporte BOM, UTF-8, Windows-1252, virgule, point-virgule, tabulation.
"""

from __future__ import annotations

import csv
import io

from ..models import (
    CanonicalDeckImport,
    CanonicalEntry,
    ImportSource,
    ImportStatistics,
    Zone,
)
from .base import MAX_ENTRIES, MAX_LINES, BaseParser

# ── Alias de colonnes ─────────────────────────────────────────────────────────

_ALIAS_QTY = {"quantity", "qty", "count", "amount", "copies"}
_ALIAS_NAME = {"name", "card name", "card", "product name"}
_ALIAS_SET = {"set", "set code", "edition", "expansion"}
_ALIAS_NUM = {"collector number", "card number", "number", "cn", "collector_number"}
_ALIAS_LANG = {"language", "lang"}
_ALIAS_COND = {"condition", "card condition", "quality"}
_ALIAS_FOIL = {"foil", "is foil", "finish", "printing", "etched"}
_ALIAS_ZONE = {"board", "zone", "section", "deck section"}
_ALIAS_SCRYFALL = {"scryfall id", "scryfall_id"}
_ALIAS_ORACLE = {"oracle id", "oracle_id"}
_ALIAS_CM = {"cardmarket id", "cardmarket_id", "product id", "uuid"}

# Mapping alias → champ canonique
_COLUMN_MAP: list[tuple[set[str], str]] = [
    (_ALIAS_QTY, "quantity"),
    (_ALIAS_NAME, "name"),
    (_ALIAS_SET, "set_code"),
    (_ALIAS_NUM, "collector_number"),
    (_ALIAS_LANG, "language"),
    (_ALIAS_COND, "condition"),
    (_ALIAS_FOIL, "foil"),
    (_ALIAS_ZONE, "zone"),
    (_ALIAS_SCRYFALL, "scryfall_id"),
    (_ALIAS_ORACLE, "oracle_id"),
    (_ALIAS_CM, "cardmarket_id"),
]

# Zones connues dans les CSV
_ZONE_MAP: dict[str, Zone] = {
    "commander": Zone.COMMANDER,
    "mainboard": Zone.MAINBOARD,
    "main": Zone.MAINBOARD,
    "sideboard": Zone.SIDEBOARD,
    "side": Zone.SIDEBOARD,
    "maybeboard": Zone.MAYBEBOARD,
    "maybe": Zone.MAYBEBOARD,
    "companion": Zone.COMPANION,
    "token": Zone.TOKEN,
}


def _map_header(header: str) -> str | None:
    key = header.strip().lower()
    for aliases, field_name in _COLUMN_MAP:
        if key in aliases:
            return field_name
    return None


def _detect_separator(sample: str) -> str:
    counts = {
        "\t": sample.count("\t"),
        ";": sample.count(";"),
        ",": sample.count(","),
    }
    return max(counts, key=lambda k: counts[k])


def _parse_finish(val: str) -> str | None:
    v = val.strip().lower()
    if v in {"true", "yes", "1", "foil"}:
        return "foil"
    if v in {"etched"}:
        return "etched"
    if v in {"false", "no", "0", "nonfoil", "non-foil"}:
        return "nonfoil"
    return None


class CsvParser(BaseParser):
    source_name = "csv"
    supported_extensions = [".csv", ".tsv"]
    supported_mime_types = ["text/csv", "text/tab-separated-values", "text/plain"]

    def can_parse(self, raw: str | bytes) -> int:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)
        sample = "\n".join(raw.splitlines()[:3])
        sep = _detect_separator(sample)
        parts = sample.split("\n")[0].split(sep) if sample else []
        if len(parts) < 2:
            return 0
        mapped = sum(1 for p in parts if _map_header(p) is not None)
        if mapped >= 2:
            return 90
        if mapped == 1:
            return 40
        return 0

    def parse(self, raw: str | bytes) -> CanonicalDeckImport:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)

        result = CanonicalDeckImport(
            source=ImportSource.CSV,
            source_format="CSV",
        )
        stats = ImportStatistics()

        lines = raw.splitlines()
        if len(lines) > MAX_LINES:
            result.errors.append(f"Too many lines: {len(lines)} (max {MAX_LINES})")
            lines = lines[:MAX_LINES]

        stats.lines_received = len(lines)

        if not lines:
            result.errors.append("Empty file")
            return result

        sample = "\n".join(lines[:5])
        sep = _detect_separator(sample)
        result.source_format = {"," : "CSV", ";" : "CSV (semicolon)", "\t": "TSV"}.get(sep, "CSV")

        reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=sep)

        headers_raw: list[str] = []
        field_index: dict[str, int] = {}
        detected_zones: set[str] = set()
        unrecognized_headers: list[str] = []

        for rownum, row in enumerate(reader, 1):
            if rownum == 1:
                headers_raw = row
                for i, h in enumerate(headers_raw):
                    mapped = _map_header(h)
                    if mapped and mapped not in field_index:
                        field_index[mapped] = i
                    elif not mapped and h.strip():
                        unrecognized_headers.append(h.strip())

                if "name" not in field_index:
                    result.errors.append(
                        "No 'name' column found. Unrecognized headers: "
                        + ", ".join(unrecognized_headers)
                    )
                    return result

                if unrecognized_headers:
                    result.warnings.append(
                        "Unrecognized columns (ignored): " + ", ".join(unrecognized_headers)
                    )
                continue

            if len(result.entries) >= MAX_ENTRIES:
                result.errors.append("Too many entries, truncating")
                break

            def get(field: str, default: str = "", _row: list = row) -> str:
                idx = field_index.get(field)
                return _row[idx].strip() if idx is not None and idx < len(_row) else default

            name = get("name")
            if not name:
                continue

            try:
                qty = int(get("quantity", "1") or "1")
            except ValueError:
                qty = 1

            # Zone
            zone_raw = get("zone").lower()
            zone = _ZONE_MAP.get(zone_raw, Zone.MAINBOARD)

            # Foil/finish
            finish = _parse_finish(get("foil")) if "foil" in field_index else None

            detected_zones.add(zone.value)

            entry = CanonicalEntry(
                line_number=rownum,
                raw_line=sep.join(row),
                quantity=qty,
                raw_name=name,
                canonical_name=name,
                set_code=get("set_code").upper() or None,
                collector_number=get("collector_number") or None,
                language=get("language") or None,
                condition=get("condition") or None,
                finish=finish,
                zone=zone,
                scryfall_id=get("scryfall_id") or None,
                oracle_id=get("oracle_id") or None,
            )

            cm_id = get("cardmarket_id")
            if cm_id:
                try:
                    entry.cardmarket_product_id = int(cm_id)
                except ValueError:
                    pass

            result.entries.append(entry)
            stats.cards_detected += 1
            stats.copies_detected += qty

        if Zone.COMMANDER.value in detected_zones:
            result.format = "commander"

        result.detected_zones = sorted(detected_zones)
        result.statistics = stats
        return result
