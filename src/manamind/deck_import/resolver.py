"""
Résolution des cartes après parsing.
Lookup dans la DB Scryfall locale selon l'ordre de priorité :
1. scryfall_id exact
2. cardmarket_product_id exact
3. set_code + collector_number
4. nom normalisé + set_code + collector_number
5. nom normalisé + set_code
6. nom normalisé uniquement
7. recherche approximative (suggestion seulement)
"""

from __future__ import annotations

import logging
import re
import unicodedata

from .models import CanonicalDeckImport, CanonicalEntry, ResolutionStatus, Zone

log = logging.getLogger(__name__)


def _normalize(name: str) -> str:
    nfd = unicodedata.normalize("NFD", name)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").strip().lower()


def _normalize_split(name: str) -> str:
    """Normalise les cartes doubles en ne gardant que la première face.

    Moxfield écrit « Bloomvine Regent / Claim Territory » avec une seule
    barre, Scryfall « Fire // Ice » avec deux : les deux formes sont admises.
    """
    return _normalize(re.split(r"\s+/{1,2}\s+|//", name)[0].strip())


def _pick_printing(rows):
    """Choisit une impression parmi celles d'un même couple édition + numéro.

    Le catalogue garde une ligne par langue : six lignes pour TDM 178 ne
    font pas une ambiguïté tant qu'elles désignent la même carte. On préfère
    l'anglais. Retourne None si plusieurs cartes différentes répondent.
    """
    if not rows:
        return None
    if len({r[1] for r in rows}) > 1:
        return None
    return next((r for r in rows if r[8] == "en"), rows[0])


def resolve(deck: CanonicalDeckImport) -> CanonicalDeckImport:
    """
    Résout les cartes d'un CanonicalDeckImport en interrogeant la DB locale.
    Modifie les entrées in-place et met à jour les statistiques.
    Retourne le deck modifié.
    """
    try:
        from contextlib import contextmanager

        from ..db.engine import SessionLocal

        if SessionLocal is None:
            deck.warnings.append("DB unavailable — resolution skipped")
            return deck

        @contextmanager
        def _session():
            s = SessionLocal()
            try:
                yield s
            finally:
                s.close()

    except ImportError:
        deck.warnings.append("DB unavailable — resolution skipped")
        return deck

    exact = 0
    ambiguous = 0
    unresolved = 0

    with _session() as sess:
        for entry in deck.entries:
            try:
                _resolve_entry(entry, sess)
            except Exception as exc:
                log.warning("Resolution error for %r: %s", entry.raw_name, exc)
                entry.resolution_status = ResolutionStatus.UNRESOLVED
                entry.warnings.append(f"Resolution error: {exc}")

            if entry.resolution_status in (
                ResolutionStatus.EXACT_IDENTIFIER,
                ResolutionStatus.EXACT_PRINTING,
                ResolutionStatus.EXACT_CARD_UNKNOWN_PRINTING,
            ):
                exact += 1
            elif entry.resolution_status == ResolutionStatus.AMBIGUOUS:
                ambiguous += 1
            elif entry.resolution_status in (
                ResolutionStatus.UNRESOLVED,
                ResolutionStatus.PROBABLE_MATCH,
            ):
                unresolved += 1

        try:
            _infer_commanders(deck, sess)
        except Exception as exc:
            log.warning("Commander inference error: %s", exc)

    deck.statistics.exact_matches = exact
    deck.statistics.ambiguous_matches = ambiguous
    deck.statistics.unresolved_entries = unresolved
    return deck


def _resolve_entry(entry: CanonicalEntry, sess) -> None:
    """Résout une entrée unique. Modifie entry in-place."""
    from sqlalchemy import text

    # ── 1. scryfall_id exact ─────────────────────────────────────────────────
    if entry.scryfall_id:
        row = sess.execute(text("""
            SELECT p.scryfall_id, p.oracle_id, c.name, c.normalized_name,
                   p.set_code, p.collector_number, p.digital, p.cardmarket_id
            FROM scryfall_card_printings p
            JOIN scryfall_cards c ON c.id = p.card_id
            WHERE p.scryfall_id = :sid
        """), {"sid": entry.scryfall_id}).fetchone()
        if row:
            _apply_printing(entry, row)
            entry.resolution_status = ResolutionStatus.EXACT_IDENTIFIER
            entry.confidence = 100
            return

    # ── 2. cardmarket_product_id exact ───────────────────────────────────────
    if entry.cardmarket_product_id:
        row = sess.execute(text("""
            SELECT p.scryfall_id, p.oracle_id, c.name, c.normalized_name,
                   p.set_code, p.collector_number, p.digital, p.cardmarket_id
            FROM scryfall_card_printings p
            JOIN scryfall_cards c ON c.id = p.card_id
            WHERE p.cardmarket_id = :cid
        """), {"cid": entry.cardmarket_product_id}).fetchone()
        if row:
            _apply_printing(entry, row)
            entry.resolution_status = ResolutionStatus.EXACT_IDENTIFIER
            entry.confidence = 100
            return

    # ── 3. set_code + collector_number (sans nom) ────────────────────────────
    if entry.set_code and entry.collector_number:
        rows = sess.execute(text("""
            SELECT p.scryfall_id, p.oracle_id, c.name, c.normalized_name,
                   p.set_code, p.collector_number, p.digital, p.cardmarket_id,
                   p.lang
            FROM scryfall_card_printings p
            JOIN scryfall_cards c ON c.id = p.card_id
            WHERE UPPER(p.set_code) = UPPER(:set_code)
              AND LOWER(p.collector_number) = LOWER(:col_num)
        """), {"set_code": entry.set_code, "col_num": entry.collector_number}).fetchall()

        row = _pick_printing(rows)
        if row is not None:
            _apply_printing(entry, row)
            entry.canonical_name = row[2]  # nom canonique Scryfall
            # Vérifier cohérence avec le nom fourni
            if entry.raw_name and not _names_match(entry.raw_name, row[2]):
                entry.warnings.append(
                    f"Name mismatch: provided {entry.raw_name!r}, found {row[2]!r}"
                )
            entry.resolution_status = ResolutionStatus.EXACT_PRINTING
            entry.confidence = 95 if entry.raw_name else 85
            if row[6]:  # digital
                entry.resolution_status = ResolutionStatus.UNSUPPORTED_DIGITAL_CARD
                entry.warnings.append("Digital-only card (not available in paper)")
            return
        if rows:
            # Plusieurs cartes distinctes sous le même numéro : on laisse le
            # nom trancher aux étapes suivantes plutôt que de bloquer ici.
            entry.warnings.append(f"Ambiguous set+number: {len(rows)} printings found")

    # ── 4. nom normalisé + set_code + collector_number ───────────────────────
    if entry.raw_name and entry.set_code and entry.collector_number:
        norm = _normalize_split(entry.raw_name)
        rows = sess.execute(text("""
            SELECT p.scryfall_id, p.oracle_id, c.name, c.normalized_name,
                   p.set_code, p.collector_number, p.digital, p.cardmarket_id,
                   p.lang
            FROM scryfall_card_printings p
            JOIN scryfall_cards c ON c.id = p.card_id
            WHERE UPPER(p.set_code) = UPPER(:set_code)
              AND LOWER(p.collector_number) = LOWER(:col_num)
              AND (LOWER(c.normalized_name) = :norm
                   OR LOWER(c.name) = :raw_lower
                   OR split_part(LOWER(c.normalized_name), ' // ', 1) = :norm)
            ORDER BY (p.lang = 'en') DESC
        """), {
            "set_code": entry.set_code,
            "col_num": entry.collector_number,
            "norm": norm,
            "raw_lower": entry.raw_name.lower(),
        }).fetchall()

        if rows:
            row = rows[0]
            _apply_printing(entry, row)
            entry.canonical_name = row[2]
            entry.resolution_status = ResolutionStatus.EXACT_PRINTING
            entry.confidence = 95
            if row[6]:
                entry.resolution_status = ResolutionStatus.UNSUPPORTED_DIGITAL_CARD
                entry.warnings.append("Digital-only card (not available in paper)")
            return

    # ── 5. nom normalisé + set_code ─────────────────────────────────────────
    if entry.raw_name and entry.set_code:
        norm = _normalize_split(entry.raw_name)
        rows = sess.execute(text("""
            SELECT p.scryfall_id, p.oracle_id, c.name, c.normalized_name,
                   p.set_code, p.collector_number, p.digital, p.cardmarket_id
            FROM scryfall_card_printings p
            JOIN scryfall_cards c ON c.id = p.card_id
            WHERE UPPER(p.set_code) = UPPER(:set_code)
              AND (LOWER(c.normalized_name) = :norm
                   OR LOWER(c.name) = :raw_lower
                   OR split_part(LOWER(c.normalized_name), ' // ', 1) = :norm)
            ORDER BY (p.lang = 'en') DESC, p.released_at DESC
            LIMIT 5
        """), {
            "set_code": entry.set_code,
            "norm": norm,
            "raw_lower": entry.raw_name.lower(),
        }).fetchall()

        if len(rows) == 1:
            row = rows[0]
            _apply_printing(entry, row)
            entry.canonical_name = row[2]
            entry.resolution_status = ResolutionStatus.EXACT_CARD_UNKNOWN_PRINTING
            entry.confidence = 70
            return
        if len(rows) > 1:
            row = rows[0]
            _apply_printing(entry, row)
            entry.canonical_name = row[2]
            entry.resolution_status = ResolutionStatus.EXACT_CARD_UNKNOWN_PRINTING
            entry.confidence = 65
            entry.warnings.append(f"Multiple printings in {entry.set_code}: used most recent")
            return

    # ── 6. nom normalisé uniquement ──────────────────────────────────────────
    if entry.raw_name:
        norm = _normalize_split(entry.raw_name)
        rows = sess.execute(text("""
            SELECT c.oracle_id, c.name, c.normalized_name, c.type_line
            FROM scryfall_cards c
            WHERE LOWER(c.normalized_name) = :norm
               OR LOWER(c.name) = :raw_lower
            LIMIT 5
        """), {"norm": norm, "raw_lower": entry.raw_name.lower()}).fetchall()
        # Un jeton ou une carte d'art peut porter le nom d'une vraie carte
        # (jeton « Llanowar Elves » de TSR) : la carte jouable l'emporte.
        playable = [r for r in rows if not (r[3] or "").startswith(("Token", "Card"))]
        if playable:
            rows = playable

        # Repli sur la face avant : une liste ne donne souvent que le recto
        # d'une carte double (« Bloomvine Regent » pour « Bloomvine Regent //
        # Claim Territory »). Les cartes d'art (« X // X », type « Card // Card »)
        # sont écartées, sans quoi chaque recto deviendrait ambigu.
        if not rows:
            rows = sess.execute(text("""
                SELECT c.oracle_id, c.name, c.normalized_name
                FROM scryfall_cards c
                WHERE c.normalized_name LIKE '% // %'
                  AND split_part(LOWER(c.normalized_name), ' // ', 1) = :norm
                  AND c.type_line NOT LIKE 'Card%'
                LIMIT 3
            """), {"norm": norm}).fetchall()

        if len(rows) == 1:
            entry.oracle_id = rows[0][0]
            entry.canonical_name = rows[0][1]
            entry.resolution_status = ResolutionStatus.EXACT_CARD_UNKNOWN_PRINTING
            entry.confidence = 50
            return
        if len(rows) > 1:
            entry.oracle_id = rows[0][0]
            entry.canonical_name = rows[0][1]
            entry.resolution_status = ResolutionStatus.PROBABLE_MATCH
            entry.confidence = 45
            entry.warnings.append("Multiple cards with similar name found")
            return

    # ── 7. Fuzzy (suggestion) ────────────────────────────────────────────────
    if entry.raw_name:
        norm = _normalize_split(entry.raw_name)
        rows = sess.execute(text("""
            SELECT c.oracle_id, c.name
            FROM scryfall_cards c
            WHERE LOWER(c.normalized_name) LIKE :prefix
            LIMIT 5
        """), {"prefix": norm[:8] + "%"}).fetchall()

        if rows:
            entry.warnings.append(
                "Unresolved — possible matches: " + ", ".join(r[1] for r in rows[:3])
            )

    entry.resolution_status = ResolutionStatus.UNRESOLVED
    entry.confidence = 0


TRAILING_BLOCK_TAG = "bloc_final"
_MAX_COMMANDERS = 2


def _commander_candidates(entries: list[CanonicalEntry]) -> list[CanonicalEntry]:
    """Repère les lignes que l'export place à part sans les étiqueter.

    Moxfield ne marque pas les commandants dans ses exports texte, mais les
    isole : en fin de liste après une ligne vide (export MTGO, texte simple),
    ou en tête devant une liste par ailleurs alphabétique (export avec
    éditions). Retourne ces lignes, ou une liste vide si rien ne se détache.
    """
    trailing = [e for e in entries if TRAILING_BLOCK_TAG in e.tags]
    if 0 < len(trailing) <= _MAX_COMMANDERS and len(trailing) < len(entries):
        return trailing

    def _sorted(block: list[CanonicalEntry]) -> bool:
        keys = [_normalize(e.raw_name) for e in block]
        return keys == sorted(keys)

    if len(entries) < 10 or _sorted(entries):
        return []
    for k in range(1, _MAX_COMMANDERS + 1):
        if _sorted(entries[k:]):
            return entries[:k]
    return []


def _can_lead(type_line: str, oracle_text: str) -> bool:
    """Une carte peut-elle occuper la zone de commandement ?"""
    t = type_line.split("//")[0]
    return (
        ("Legendary" in t and "Creature" in t)
        or "Background" in t
        or "can be your commander" in oracle_text
    )


def _infer_commanders(deck: CanonicalDeckImport, sess) -> None:
    """Range en zone commandant les cartes que la liste isole sans le dire.

    Ne fait rien si une zone commandant existe déjà, ou si une des cartes
    isolées ne peut pas être commandant : mieux vaut ne rien déduire que
    d'analyser un deck sous un faux commandant.
    """
    from sqlalchemy import text

    if any(e.zone == Zone.COMMANDER for e in deck.entries):
        return
    main = [e for e in deck.entries if e.zone == Zone.MAINBOARD]
    candidates = _commander_candidates(main)
    if not candidates or any(e.quantity != 1 or not e.oracle_id for e in candidates):
        return

    rows = sess.execute(text("""
        SELECT oracle_id, type_line, COALESCE(oracle_text, '')
        FROM scryfall_cards
        WHERE oracle_id = ANY(:ids)
    """), {"ids": [str(e.oracle_id) for e in candidates]}).fetchall()
    eligible = {str(r[0]) for r in rows if _can_lead(r[1] or "", r[2])}
    if not all(str(e.oracle_id) in eligible for e in candidates):
        return

    for e in candidates:
        e.zone = Zone.COMMANDER
        e.warnings.append("Commandant déduit de sa place dans la liste")
    deck.format = "commander"
    deck.detected_zones = sorted(set(deck.detected_zones) | {Zone.COMMANDER.value})


def _apply_printing(entry: CanonicalEntry, row) -> None:
    """Applique les données d'une impression à une entrée."""
    entry.scryfall_id = row[0]
    entry.oracle_id = row[1]
    # Ne pas écraser le canonical_name ici — fait par l'appelant si nécessaire
    entry.set_code = entry.set_code or row[4]
    entry.collector_number = entry.collector_number or row[5]
    if row[7]:  # cardmarket_id
        entry.cardmarket_product_id = entry.cardmarket_product_id or row[7]


def _names_match(provided: str, canonical: str) -> bool:
    """Vérifie si deux noms de carte correspondent (insensible à la casse, diacritiques)."""
    p = _normalize_split(provided)
    c = _normalize_split(canonical)
    return p == c or p.startswith(c) or c.startswith(p)
