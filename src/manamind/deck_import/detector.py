"""Détection automatique du format d'une decklist par analyse du contenu."""

from __future__ import annotations

import re

from .models import DetectionResult, ImportSource

# ── Signatures XML ──────────────────────────────────────────────────────────

_RE_COCKATRICE = re.compile(r"<cockatrice_deck", re.IGNORECASE)
_RE_MTGO_DEK = re.compile(r"<Deck>.*<Cards\s+CatID=", re.DOTALL | re.IGNORECASE)
_RE_XML_DECL = re.compile(r"<\?xml", re.IGNORECASE)

# ── Signatures Forge ─────────────────────────────────────────────────────────
_RE_FORGE_METADATA = re.compile(r"^\[metadata\]", re.MULTILINE | re.IGNORECASE)
_RE_FORGE_MAIN = re.compile(r"^\[main\]", re.MULTILINE | re.IGNORECASE)
_RE_FORGE_COMMANDER = re.compile(r"^\[commander\]", re.MULTILINE | re.IGNORECASE)

# ── Signatures XMage ─────────────────────────────────────────────────────────
# Ligne type : "1 [CMM:396] Sol Ring"
_RE_XMAGE_LINE = re.compile(r"^\d+\s+\[[A-Z0-9]+:\d+[a-z]?\]\s+\S", re.MULTILINE)

# ── Signatures Arena ─────────────────────────────────────────────────────────
# En-têtes + lignes avec (SET) numéro
_RE_ARENA_HEADER = re.compile(
    r"^(Commander|Deck|Sideboard|Maybeboard)\s*$", re.MULTILINE | re.IGNORECASE
)
_RE_ARENA_LINE = re.compile(r"^\d+x?\s+.+\([A-Z0-9]{2,6}\)\s+\d+", re.MULTILINE)

# ── Signatures MTGO texte ────────────────────────────────────────────────────
_RE_MTGO_SB = re.compile(r"^SB:\s+\d+\s+", re.MULTILINE)

# ── Signatures texte structuré générique ─────────────────────────────────────
_RE_SECTION_HEADER = re.compile(
    r"^(//\s*)?(Commander|Commanders?|Deck|Main|Mainboard|Sideboard|Side|"
    r"Maybeboard|Maybe|Companion|Reserve|Tokens?)\s*:?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_RE_CARD_LINE = re.compile(r"^\d+x?\s+\S", re.MULTILINE)

# ── CSV ──────────────────────────────────────────────────────────────────────
_CSV_HEADERS = {
    "name", "card", "card name", "qty", "quantity", "count", "amount",
    "set", "set code", "edition", "foil", "condition", "language",
}

# ── JSON ─────────────────────────────────────────────────────────────────────
_RE_JSON_START = re.compile(r"^\s*[\[{]")


def detect(raw: str | bytes) -> DetectionResult:
    """Détecte le format d'une decklist à partir de son contenu brut."""
    if isinstance(raw, bytes):
        text = _decode(raw)
    else:
        text = raw

    text_stripped = text.strip()
    candidates: list[tuple[int, ImportSource, str, list[str]]] = []  # (score, source, format, reasons)

    # ── 1. XML ───────────────────────────────────────────────────────────────
    if _RE_XML_DECL.match(text_stripped) or text_stripped.startswith("<"):
        if _RE_COCKATRICE.search(text_stripped):
            candidates.append((99, ImportSource.COCKATRICE, "Cockatrice .cod XML", ["<cockatrice_deck> tag found"]))
        elif _RE_MTGO_DEK.search(text_stripped):
            candidates.append((99, ImportSource.MTGO, "Magic Online .dek XML", ["<Deck><Cards CatID=> found"]))
        else:
            candidates.append((40, ImportSource.UNKNOWN, "Unknown XML", ["XML declaration found"]))

    # ── 2. Forge .dck ────────────────────────────────────────────────────────
    if _RE_FORGE_METADATA.search(text_stripped) and _RE_FORGE_MAIN.search(text_stripped):
        reasons = ["[metadata] and [main] sections found"]
        if _RE_FORGE_COMMANDER.search(text_stripped):
            reasons.append("[commander] section found")
        candidates.append((98, ImportSource.FORGE, "Forge .dck", reasons))

    # ── 3. XMage .dck ────────────────────────────────────────────────────────
    xmage_matches = _RE_XMAGE_LINE.findall(text_stripped)
    if xmage_matches:
        score = min(95, 70 + len(xmage_matches) * 5)
        candidates.append((score, ImportSource.XMAGE, "XMage .dck", [f"{len(xmage_matches)} [SET:num] lines found"]))

    # ── 4. CSV / TSV ─────────────────────────────────────────────────────────
    csv_score = _score_csv(text_stripped)
    if csv_score > 0:
        sep = _detect_csv_separator(text_stripped)
        fmt = {"comma": "CSV", "semicolon": "CSV (semicolon)", "tab": "TSV"}.get(sep, "CSV")
        candidates.append((csv_score, ImportSource.CSV, fmt, [f"CSV headers detected, separator={sep}"]))

    # ── 5. JSON ──────────────────────────────────────────────────────────────
    if _RE_JSON_START.match(text_stripped):
        try:
            import json
            json.loads(text_stripped)
            candidates.append((97, ImportSource.JSON, "JSON", ["Valid JSON structure"]))
        except (ValueError, OverflowError):
            pass

    # ── 6. Arena (en-têtes + lignes SET+num) ─────────────────────────────────
    arena_headers = _RE_ARENA_HEADER.findall(text_stripped)
    arena_lines = _RE_ARENA_LINE.findall(text_stripped)
    if arena_headers and arena_lines:
        score = min(90, 60 + len(arena_lines) * 3)
        candidates.append((score, ImportSource.ARENA, "MTG Arena", [
            f"{len(arena_headers)} section headers",
            f"{len(arena_lines)} Arena-style card lines",
        ]))

    # ── 7. MTGO texte avec SB: ───────────────────────────────────────────────
    sb_matches = _RE_MTGO_SB.findall(text_stripped)
    if sb_matches:
        candidates.append((75, ImportSource.MTGO, "Magic Online text", [f"{len(sb_matches)} SB: lines found"]))

    # ── 8. Texte structuré générique ─────────────────────────────────────────
    section_headers = _RE_SECTION_HEADER.findall(text_stripped)
    card_lines = _RE_CARD_LINE.findall(text_stripped)
    if card_lines:
        score = 30 + min(40, len(card_lines) * 2)
        if section_headers:
            score = min(score + 20, 80)
        candidates.append((score, ImportSource.GENERIC_TEXT, "Generic text", [
            f"{len(card_lines)} card lines",
            f"{len(section_headers)} section headers",
        ]))

    # ── Sélectionner le meilleur candidat ────────────────────────────────────
    if not candidates:
        return DetectionResult(
            source=ImportSource.UNKNOWN,
            source_format="unknown",
            confidence=0,
            reasons=["No recognizable pattern found"],
        )

    candidates.sort(key=lambda c: c[0], reverse=True)
    best_score, best_source, best_format, best_reasons = candidates[0]

    alternatives = [
        {"source": s.value, "format": f, "confidence": sc}
        for sc, s, f, _ in candidates[1:4]
    ]

    return DetectionResult(
        source=best_source,
        source_format=best_format,
        confidence=best_score,
        alternatives=alternatives,
        reasons=best_reasons,
    )


def _decode(raw: bytes) -> str:
    """Décode bytes en str, gère BOM UTF-8 et Windows-1252 en fallback."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _score_csv(text: str) -> int:
    """Retourne un score CSV (0 = pas un CSV)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0
    first = lines[0].lower()
    sep = _detect_csv_separator(text)
    parts = _split_csv_line(first, sep)
    if len(parts) < 2:
        return 0
    matches = sum(1 for p in parts if p.strip().strip('"') in _CSV_HEADERS)
    if matches >= 2:
        return 85 + min(10, matches * 3)
    if matches == 1 and len(parts) >= 3:
        return 50
    return 0


def _detect_csv_separator(text: str) -> str:
    lines = [ln for ln in text.splitlines()[:5] if ln.strip()]
    if not lines:
        return "comma"
    sample = "\n".join(lines)
    counts = {
        "comma": sample.count(","),
        "semicolon": sample.count(";"),
        "tab": sample.count("\t"),
    }
    return max(counts, key=lambda k: counts[k])


def _split_csv_line(line: str, sep: str) -> list[str]:
    char = {"comma": ",", "semicolon": ";", "tab": "\t"}.get(sep, ",")
    return line.split(char)
