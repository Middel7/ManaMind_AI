"""
Parseur JSON — accepte tableau ou objet avec propriétés cards/entries/deck/mainboard.
Validation de structure stricte pour éviter l'injection via JSON malformé.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import (
    CanonicalDeckImport,
    CanonicalEntry,
    ImportSource,
    ImportStatistics,
    Zone,
)
from .base import MAX_ENTRIES, BaseParser

_MAX_JSON_BYTES = 1 * 1024 * 1024  # 1 Mo

_ZONE_MAP: dict[str, Zone] = {
    "commander": Zone.COMMANDER,
    "mainboard": Zone.MAINBOARD,
    "main": Zone.MAINBOARD,
    "sideboard": Zone.SIDEBOARD,
    "side": Zone.SIDEBOARD,
    "maybeboard": Zone.MAYBEBOARD,
    "companion": Zone.COMPANION,
}

# Propriétés de liste reconnues dans un objet JSON
_LIST_PROPS = ["cards", "entries", "deck", "mainboard", "main", "sideboard", "maybeboard"]

# Alias de champs dans un objet carte JSON
_NAME_KEYS = {"name", "card_name", "card", "cardname", "product_name"}
_QTY_KEYS = {"quantity", "qty", "count", "amount", "copies"}
_SET_KEYS = {"set", "set_code", "edition", "expansion"}
_NUM_KEYS = {"collector_number", "card_number", "number", "cn"}
_ZONE_KEYS = {"board", "zone", "section"}
_FOIL_KEYS = {"foil", "is_foil", "finish", "printing"}
_LANG_KEYS = {"language", "lang"}
_COND_KEYS = {"condition", "quality"}
_SCRY_KEYS = {"scryfall_id", "id"}
_OID_KEYS = {"oracle_id"}
_CM_KEYS = {"cardmarket_id", "product_id"}


def _get(obj: dict, keys: set[str], default: Any = None) -> Any:
    for k in keys:
        if k in obj:
            return obj[k]
    return default


def _parse_finish(val: Any) -> str | None:
    if isinstance(val, bool):
        return "foil" if val else "nonfoil"
    if isinstance(val, str):
        v = val.lower()
        if v in {"true", "yes", "1", "foil"}:
            return "foil"
        if v == "etched":
            return "etched"
        if v in {"false", "no", "0", "nonfoil", "non-foil"}:
            return "nonfoil"
    return None


class JsonParser(BaseParser):
    source_name = "json"
    supported_extensions = [".json"]
    supported_mime_types = ["application/json"]

    def can_parse(self, raw: str | bytes) -> int:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)
        stripped = raw.strip()
        if not (stripped.startswith("[") or stripped.startswith("{")):
            return 0
        try:
            data = json.loads(stripped)
            if isinstance(data, list) and data:
                return 95
            if isinstance(data, dict):
                for prop in _LIST_PROPS:
                    if prop in data and isinstance(data[prop], list):
                        return 95
                return 50
        except (ValueError, OverflowError):
            return 0
        return 0

    def parse(self, raw: str | bytes) -> CanonicalDeckImport:
        if isinstance(raw, bytes):
            raw = self.decode_bytes(raw)

        result = CanonicalDeckImport(
            source=ImportSource.JSON,
            source_format="JSON",
        )

        if len(raw.encode("utf-8")) > _MAX_JSON_BYTES:
            result.errors.append(f"JSON file too large (max {_MAX_JSON_BYTES // 1024} KB)")
            return result

        try:
            data = json.loads(raw)
        except (ValueError, OverflowError) as exc:
            result.errors.append(f"JSON parse error: {exc}")
            return result

        # Clés de zones pouvant être des listes distinctes dans l'objet
        _ZONE_PROP_KEYS = ["commander", "mainboard", "main", "sideboard", "side", "maybeboard", "companion"]

        # Extraction des entrées
        # Format 1 : tableau de cartes directement
        # Format 2 : objet avec propriétés "cards", "entries", etc.
        # Format 3 : objet avec propriétés de zones {"mainboard": [...], "sideboard": [...]}
        if isinstance(data, list):
            zoned_entries: list[tuple] = [(item, None) for item in data]
        elif isinstance(data, dict):
            # Métadonnées optionnelles
            result.deck_name = data.get("name") or data.get("deck_name") or data.get("deckName")
            result.format = (data.get("format") or "").lower() or None

            # Vérifier si les zones sont des propriétés distinctes
            has_zone_props = any(k in data and isinstance(data[k], list) for k in _ZONE_PROP_KEYS)
            if has_zone_props:
                zoned_entries = []
                for zone_key in _ZONE_PROP_KEYS:
                    if zone_key in data and isinstance(data[zone_key], list):
                        zone_override = _ZONE_MAP.get(zone_key, Zone.MAINBOARD)
                        for item in data[zone_key]:
                            zoned_entries.append((item, zone_override))
            else:
                # Chercher une liste de cartes unique
                flat_list = []
                for prop in _LIST_PROPS:
                    if prop in data and isinstance(data[prop], list):
                        flat_list = data[prop]
                        break
                if not flat_list:
                    result.errors.append("No card list found in JSON (expected 'cards', 'entries', 'mainboard', etc.)")
                    return result
                zoned_entries = [(item, None) for item in flat_list]
        else:
            result.errors.append("JSON must be an array or an object")
            return result

        if len(zoned_entries) > MAX_ENTRIES:
            result.warnings.append(f"Truncating to {MAX_ENTRIES} entries")
            zoned_entries = zoned_entries[:MAX_ENTRIES]

        stats = ImportStatistics(lines_received=len(zoned_entries))
        detected_zones: set[str] = set()

        for lineno, (item, zone_override) in enumerate(zoned_entries, 1):
            if not isinstance(item, dict):
                result.warnings.append(f"Entry {lineno} is not an object, skipping")
                stats.ignored_entries += 1
                continue

            name = str(_get(item, _NAME_KEYS, "")).strip()
            if not name:
                result.warnings.append(f"Entry {lineno} has no card name, skipping")
                stats.ignored_entries += 1
                continue

            try:
                qty = int(_get(item, _QTY_KEYS, 1))
            except (TypeError, ValueError):
                qty = 1

            if zone_override is not None:
                zone = zone_override
            else:
                zone_raw = str(_get(item, _ZONE_KEYS, "mainboard")).lower()
                zone = _ZONE_MAP.get(zone_raw, Zone.MAINBOARD)
            detected_zones.add(zone.value)

            set_code = str(_get(item, _SET_KEYS, "") or "").upper() or None
            col_num = str(_get(item, _NUM_KEYS, "") or "") or None
            language = str(_get(item, _LANG_KEYS, "") or "") or None
            condition = str(_get(item, _COND_KEYS, "") or "") or None
            finish = _parse_finish(_get(item, _FOIL_KEYS))
            scryfall_id = str(_get(item, _SCRY_KEYS, "") or "") or None
            oracle_id = str(_get(item, _OID_KEYS, "") or "") or None

            entry = CanonicalEntry(
                line_number=lineno,
                raw_line=json.dumps(item, ensure_ascii=False),
                quantity=qty,
                raw_name=name,
                canonical_name=name,
                set_code=set_code,
                collector_number=col_num,
                language=language,
                condition=condition,
                finish=finish,
                zone=zone,
                scryfall_id=scryfall_id if (scryfall_id and len(scryfall_id) == 36) else None,
                oracle_id=oracle_id if (oracle_id and len(oracle_id) == 36) else None,
            )

            cm_id = _get(item, _CM_KEYS)
            if cm_id is not None:
                try:
                    entry.cardmarket_product_id = int(cm_id)
                except (TypeError, ValueError):
                    pass

            result.entries.append(entry)
            stats.cards_detected += 1
            stats.copies_detected += qty

        if Zone.COMMANDER.value in detected_zones:
            result.format = result.format or "commander"

        result.detected_zones = sorted(detected_zones)
        result.statistics = stats
        return result
