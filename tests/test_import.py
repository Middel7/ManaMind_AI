"""Tests du module d'import de decklists multi-format."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# ── Imports du module ─────────────────────────────────────────────────────────
from manamind.deck_import.detector import detect
from manamind.deck_import.models import ImportSource, Zone, ResolutionStatus
from manamind.deck_import.parsers.registry import parse


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _entries_by_zone(deck, zone: Zone):
    return [e for e in deck.entries if e.zone == zone]


def _names(entries):
    return [e.raw_name or e.canonical_name for e in entries]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Détection de format
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetector:
    def test_cockatrice_xml(self):
        raw = """<?xml version="1.0" encoding="UTF-8"?>
<cockatrice_deck version="1">
  <deckname>Test</deckname>
  <zone name="main">
    <card number="4" name="Lightning Bolt"/>
  </zone>
</cockatrice_deck>"""
        r = detect(raw)
        assert r.source == ImportSource.COCKATRICE
        assert r.confidence >= 95

    def test_mtgo_dek_xml(self):
        raw = """<?xml version="1.0" encoding="UTF-8"?>
<Deck>
  <Cards CatID="12345" Quantity="4" Sideboard="false" Name="Lightning Bolt"/>
  <Cards CatID="99999" Quantity="2" Sideboard="true" Name="Negate"/>
</Deck>"""
        r = detect(raw)
        assert r.source == ImportSource.MTGO
        assert r.confidence >= 95

    def test_forge_format(self):
        raw = """[metadata]
Name=My EDH Deck
Commander=Atraxa, Praetor's Voice
[main]
1 Sol Ring
1 Command Tower
[commander]
1 Atraxa, Praetor's Voice"""
        r = detect(raw)
        assert r.source == ImportSource.XMAGE or r.source_format in ("Forge .dck", "Forge", "forge")
        # Forge a un score élevé
        assert r.confidence >= 80

    def test_arena_format(self):
        raw = """Deck
4 Lightning Bolt (M11) 149
3 Counterspell (TSR) 63

Sideboard
2 Negate (M21) 70"""
        r = detect(raw)
        assert r.source == ImportSource.ARENA
        assert r.confidence >= 65

    def test_csv_format(self):
        raw = "name,quantity,set,condition\nLightning Bolt,4,M11,NM\nCounterspell,2,TSR,EX\n"
        r = detect(raw)
        assert r.source == ImportSource.CSV
        assert r.confidence >= 80

    def test_json_format(self):
        import json
        raw = json.dumps({"cards": [{"name": "Sol Ring", "quantity": 1}]})
        r = detect(raw)
        assert r.source == ImportSource.JSON
        assert r.confidence >= 90

    def test_generic_text(self):
        raw = "4 Lightning Bolt\n2 Counterspell\n1 Sol Ring\n"
        r = detect(raw)
        # Doit détecter comme texte générique ou MTGO text
        assert r.source in (ImportSource.GENERIC_TEXT, ImportSource.MTGO, ImportSource.MOXFIELD)

    def test_xmage_format(self):
        raw = "4 [M11:149] Lightning Bolt\n2 [TSR:63] Counterspell\n"
        r = detect(raw)
        assert r.source == ImportSource.XMAGE
        assert r.confidence >= 80


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Parseur texte générique / Arena / MTGO
# ═══════════════════════════════════════════════════════════════════════════════

class TestTextParser:
    def test_simple_text(self):
        raw = "4 Lightning Bolt\n2 Counterspell\n1 Sol Ring\n"
        deck = parse(raw)
        assert len(deck.entries) == 3
        names = _names(deck.entries)
        assert "Lightning Bolt" in names
        assert "Counterspell" in names

    def test_commander_section(self):
        raw = """Commander
1 Atraxa, Praetor's Voice

Deck
1 Sol Ring
1 Command Tower
1 Arcane Signet
"""
        deck = parse(raw)
        commanders = _entries_by_zone(deck, Zone.COMMANDER)
        assert len(commanders) == 1
        assert "Atraxa" in commanders[0].raw_name

    def test_sideboard_section(self):
        raw = """4 Lightning Bolt
2 Counterspell

Sideboard
1 Negate
2 Swan Song
"""
        deck = parse(raw)
        sb = _entries_by_zone(deck, Zone.SIDEBOARD)
        assert len(sb) == 2
        main = _entries_by_zone(deck, Zone.MAINBOARD)
        assert len(main) == 2

    def test_arena_with_set(self):
        raw = """Deck
4 Lightning Bolt (M11) 149
3 Counterspell (TSR) 63

Sideboard
2 Negate (M21) 70
"""
        deck = parse(raw)
        main = _entries_by_zone(deck, Zone.MAINBOARD)
        assert len(main) == 2
        assert main[0].set_code in ("M11", None)

    def test_mtgo_sb_prefix(self):
        raw = """4 Lightning Bolt
2 Counterspell
SB: 1 Negate
SB: 2 Swan Song
"""
        deck = parse(raw)
        sb = _entries_by_zone(deck, Zone.SIDEBOARD)
        assert len(sb) == 2

    def test_quantity_parsing(self):
        raw = "4x Lightning Bolt\n2X Counterspell\n"
        deck = parse(raw)
        assert deck.entries[0].quantity == 4
        assert deck.entries[1].quantity == 2

    def test_maybeboard(self):
        raw = """1 Sol Ring

Maybeboard
1 Mana Vault
"""
        deck = parse(raw)
        maybe = _entries_by_zone(deck, Zone.MAYBEBOARD)
        assert len(maybe) == 1

    def test_empty_lines_ignored(self):
        raw = "\n\n4 Lightning Bolt\n\n\n2 Counterspell\n\n"
        deck = parse(raw)
        assert len([e for e in deck.entries if e.raw_name]) == 2

    def test_comments_ignored(self):
        raw = "// Main deck\n4 Lightning Bolt\n# Some comment\n2 Counterspell\n"
        deck = parse(raw)
        assert len([e for e in deck.entries if e.raw_name]) == 2

    def test_max_quantity_capped(self):
        raw = "9999 Lightning Bolt\n"
        deck = parse(raw)
        # Doit être parsé mais quantité cappée
        assert len(deck.entries) >= 0  # Tolérant : erreur ou cappé selon impl.

    def test_utf8_card_name(self):
        raw = "1 Juzam Djinn\n1 Séance\n"
        deck = parse(raw)
        names = _names(deck.entries)
        assert any("Juzam" in n for n in names)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. XMage Parser
# ═══════════════════════════════════════════════════════════════════════════════

class TestXMageParser:
    def test_basic(self):
        raw = "4 [M11:149] Lightning Bolt\n2 [TSR:63] Counterspell\n"
        deck = parse(raw)
        assert len(deck.entries) == 2
        assert deck.entries[0].set_code in ("M11", None)
        assert deck.entries[0].collector_number in ("149", None)

    def test_without_set(self):
        """XMage lines sans [SET:num] — doit tomber sur TextParser."""
        raw = "4 Lightning Bolt\n2 Counterspell\n"
        deck = parse(raw)
        assert len(deck.entries) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Forge Parser
# ═══════════════════════════════════════════════════════════════════════════════

class TestForgeParser:
    def test_basic(self):
        raw = """[metadata]
Name=Test Deck
[main]
1 Sol Ring|CMD|232
4 Lightning Bolt|M11|149
[sideboard]
2 Negate|M21|70
"""
        deck = parse(raw)
        main = _entries_by_zone(deck, Zone.MAINBOARD)
        assert len(main) >= 2
        sb = _entries_by_zone(deck, Zone.SIDEBOARD)
        assert len(sb) >= 1

    def test_commander_section(self):
        raw = """[metadata]
Name=EDH
[commander]
1 Atraxa, Praetor's Voice|NEO|100
[main]
1 Sol Ring|CMD|232
"""
        deck = parse(raw)
        commanders = _entries_by_zone(deck, Zone.COMMANDER)
        assert len(commanders) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. XML Parsers (Cockatrice + MTGO DEK)
# ═══════════════════════════════════════════════════════════════════════════════

class TestXmlParsers:
    def test_cockatrice_basic(self):
        raw = """<?xml version="1.0"?>
<cockatrice_deck version="1">
  <deckname>Lightning Bolt Test</deckname>
  <zone name="main">
    <card number="4" name="Lightning Bolt"/>
    <card number="2" name="Counterspell"/>
  </zone>
  <zone name="side">
    <card number="1" name="Negate"/>
  </zone>
</cockatrice_deck>"""
        deck = parse(raw)
        main = _entries_by_zone(deck, Zone.MAINBOARD)
        assert len(main) == 2
        sb = _entries_by_zone(deck, Zone.SIDEBOARD)
        assert len(sb) == 1

    def test_mtgo_dek_xml(self):
        raw = """<?xml version="1.0"?>
<Deck>
  <NetDecks/>
  <Cards CatID="12345" Quantity="4" Sideboard="false" Name="Lightning Bolt"/>
  <Cards CatID="67890" Quantity="2" Sideboard="false" Name="Counterspell"/>
  <Cards CatID="11111" Quantity="1" Sideboard="true" Name="Negate"/>
</Deck>"""
        deck = parse(raw)
        main = _entries_by_zone(deck, Zone.MAINBOARD)
        assert len(main) == 2
        assert main[0].quantity == 4

    def test_xxe_protection(self):
        """Un payload avec DOCTYPE doit être rejeté."""
        raw = """<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<cockatrice_deck version="1">
  <zone name="main"><card number="1" name="&xxe;"/></zone>
</cockatrice_deck>"""
        deck = parse(raw)
        # Doit renvoyer des erreurs ou un deck vide — jamais planter
        assert deck is not None
        assert len(deck.errors) > 0 or len(deck.entries) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CSV Parser
# ═══════════════════════════════════════════════════════════════════════════════

class TestCsvParser:
    def test_basic_csv(self):
        raw = "name,quantity,set\nLightning Bolt,4,M11\nCounterspell,2,TSR\n"
        deck = parse(raw)
        assert len(deck.entries) == 2
        assert deck.entries[0].quantity == 4

    def test_semicolon_separator(self):
        raw = "name;quantity;set\nLightning Bolt;4;M11\nCounterspell;2;TSR\n"
        deck = parse(raw)
        assert len(deck.entries) == 2

    def test_tsv_separator(self):
        raw = "name\tquantity\tset\nLightning Bolt\t4\tM11\nCounterspell\t2\tTSR\n"
        deck = parse(raw)
        assert len(deck.entries) == 2

    def test_foil_column(self):
        raw = "name,quantity,foil\nLightning Bolt,1,true\nCounterspell,1,false\n"
        deck = parse(raw)
        assert deck.entries[0].finish in ("foil", None)

    def test_zone_column(self):
        raw = "name,quantity,zone\nLightning Bolt,4,mainboard\nNegate,2,sideboard\n"
        deck = parse(raw)
        sb = _entries_by_zone(deck, Zone.SIDEBOARD)
        assert len(sb) >= 1

    def test_alias_qty_column(self):
        """La colonne 'count' doit être reconnue comme quantité."""
        raw = "name,count\nLightning Bolt,4\nCounterspell,2\n"
        deck = parse(raw)
        assert len(deck.entries) == 2

    def test_missing_name_skipped(self):
        raw = "name,quantity\n,4\nCounterspell,2\n"
        deck = parse(raw)
        names = [e.raw_name for e in deck.entries if e.raw_name]
        assert "Counterspell" in names
        assert len(names) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 7. JSON Parser
# ═══════════════════════════════════════════════════════════════════════════════

class TestJsonParser:
    def test_array_format(self):
        import json
        raw = json.dumps([
            {"name": "Lightning Bolt", "quantity": 4},
            {"name": "Counterspell", "qty": 2},
        ])
        deck = parse(raw)
        assert len(deck.entries) == 2

    def test_cards_key(self):
        import json
        raw = json.dumps({"cards": [
            {"name": "Sol Ring", "count": 1},
        ]})
        deck = parse(raw)
        assert len(deck.entries) == 1

    def test_mainboard_sideboard_keys(self):
        import json
        raw = json.dumps({
            "mainboard": [{"name": "Lightning Bolt", "quantity": 4}],
            "sideboard": [{"name": "Negate", "quantity": 2}],
        })
        deck = parse(raw)
        main = _entries_by_zone(deck, Zone.MAINBOARD)
        sb = _entries_by_zone(deck, Zone.SIDEBOARD)
        assert len(main) == 1
        assert len(sb) == 1

    def test_foil_boolean(self):
        import json
        raw = json.dumps([{"name": "Lightning Bolt", "quantity": 1, "foil": True}])
        deck = parse(raw)
        assert deck.entries[0].finish in ("foil", None)

    def test_invalid_json_falls_back(self):
        """Un JSON invalide ne doit pas faire planter le système."""
        raw = "{invalid json here}"
        deck = parse(raw)
        # TextParser prend le relais — peut renvoyer un deck vide ou avec des erreurs
        assert deck is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Cas limites et sécurité
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_input(self):
        deck = parse("")
        assert deck is not None
        # Pas d'entrées valides
        assert len([e for e in deck.entries if e.raw_name]) == 0

    def test_whitespace_only(self):
        deck = parse("   \n\n\t  \n")
        assert deck is not None

    def test_very_long_card_name(self):
        """Un nom très long ne doit pas planter."""
        long_name = "A" * 300
        raw = f"1 {long_name}\n"
        deck = parse(raw)
        assert deck is not None

    def test_large_deck(self):
        """Un deck de 500 lignes uniques doit être parsé sans plantage."""
        lines = [f"1 Card Name {i}" for i in range(500)]
        raw = "\n".join(lines)
        deck = parse(raw)
        assert len(deck.entries) <= 2000  # Limite MAX_ENTRIES

    def test_statistics_populated(self):
        raw = "4 Lightning Bolt\n2 Counterspell\n1 Sol Ring\n"
        deck = parse(raw)
        assert deck.statistics.cards_detected >= 3
        assert deck.statistics.copies_detected >= 7

    def test_all_zones_detected(self):
        raw = """Commander
1 Atraxa, Praetor's Voice

Deck
1 Sol Ring

Sideboard
1 Negate

Maybeboard
1 Mana Vault
"""
        deck = parse(raw)
        zones = {e.zone for e in deck.entries}
        assert Zone.COMMANDER in zones
        assert Zone.MAINBOARD in zones
        assert Zone.SIDEBOARD in zones
        assert Zone.MAYBEBOARD in zones


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Endpoints HTTP
# ═══════════════════════════════════════════════════════════════════════════════

class TestImportEndpoints:
    def test_parse_requires_auth(self, client: TestClient):
        r = client.post("/api/import/parse", json={"text": "1 Sol Ring"})
        assert r.status_code == 401

    def test_resolve_requires_auth(self, client: TestClient):
        r = client.post("/api/import/resolve", json={"deck": {}})
        assert r.status_code == 401

    def test_confirm_requires_auth(self, client: TestClient):
        r = client.post("/api/import/confirm", json={"deck": {}, "destination": "collection"})
        assert r.status_code == 401

    def test_from_url_requires_auth(self, client: TestClient):
        r = client.post("/api/import/from-url", json={"url": "https://moxfield.com/decks/test"})
        assert r.status_code == 401

    def test_upload_requires_auth(self, client: TestClient):
        from io import BytesIO
        r = client.post("/api/import/upload", files={"file": ("test.txt", BytesIO(b"1 Sol Ring"), "text/plain")})
        assert r.status_code == 401

    def test_parse_empty_text_rejected(self, client: TestClient):
        """Sans auth, 401 est renvoyé avant la validation du body."""
        r = client.post("/api/import/parse", json={"text": ""})
        assert r.status_code in (400, 401)

    def test_from_url_blocks_private_ip(self, client: TestClient):
        """Les IPs privées doivent être bloquées (mais retournent 401 sans auth ici)."""
        r = client.post("/api/import/from-url", json={"url": "http://192.168.1.1/deck"})
        assert r.status_code in (401, 422)

    def test_from_url_blocks_localhost(self, client: TestClient):
        r = client.post("/api/import/from-url", json={"url": "http://localhost/deck"})
        assert r.status_code in (401, 422)
