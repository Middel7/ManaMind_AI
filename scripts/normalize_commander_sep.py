#!/usr/bin/env python3
"""Migration : normalise les noms de commandants dans deck_cards et commanders.

Corrections appliquées :
  1. DFC avec meme premier mot  : & -> //   (ex. "Kefka & Kefka" -> "Kefka // Kefka")
  2. DFC dont la 2e face commence par un article (The/A/An) : & -> //
     (ex. "Cosima, God of the Voyage & The Omenkeel" -> "Cosima... // The Omenkeel")
  3. Partners : ordre alphabetique canonique (ex. "Toggo & Akiri" -> "Akiri & Toggo")

Tables mises a jour : deck_cards.commander  et  commanders.name
"""
from __future__ import annotations

import os
import re
import sys

_AMP = re.compile(r"\s*&\s*")

_KNOWN_DFC_FRONTS = frozenset([
    "cosima, god of the voyage",
    "bruce banner",
    "tony stark",
    "urabrask",
    "esika, god of the tree",
    "tergrid, god of fright",
])


def _norm_word(w: str) -> str:
    return re.sub(r"[^a-zA-Z0-9']", "", w).lower()


def _is_dfc(name: str) -> bool:
    if "&" not in name:
        return False
    parts = [p.strip() for p in name.split("&", 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return False
    words0 = parts[0].split()
    words1 = parts[1].split()
    w0_first = _norm_word(words0[0])
    w1_first = _norm_word(words1[0])
    w0_last  = _norm_word(words0[-1])
    if w0_first == w1_first:
        return True
    if w0_first == w1_first.rstrip("s").rstrip("'"):
        return True
    # Dernier mot de la face 0 = premier mot de la face 1
    # (ex. "Esper Terra & Terra, Magical Adept")
    if w0_last and w0_last == w1_first:
        return True
    return parts[0].lower() in _KNOWN_DFC_FRONTS


def _normalize(name: str) -> str:
    if "&" not in name:
        return name
    if _is_dfc(name):
        return _AMP.sub(" // ", name, count=1)
    # Partners : ordre alphabetique canonique
    # Garde-fou : si un des noms contient lui-meme un '&' (ex. "Leo, Chaos & Order"),
    # le tri creerait une ambiguïte de parsing -> on laisse l'ordre d'origine
    parts = [p.strip() for p in name.split("&", 1)]
    if "&" in parts[0] or "&" in parts[1]:
        return name
    parts.sort()
    return " & ".join(parts)


def main() -> None:
    yes = "--yes" in sys.argv or "-y" in sys.argv
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("Erreur : DATABASE_URL non defini.", file=sys.stderr)
        sys.exit(1)

    import psycopg2

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor()

    # ── 1. Identifier tous les noms a corriger ───────────────────────────────
    cur.execute("SELECT DISTINCT commander FROM deck_cards WHERE commander LIKE '%&%'")
    commanders_amp = [row[0] for row in cur.fetchall()]

    to_fix_dfc      = []
    to_fix_partners = []
    for old in commanders_amp:
        new = _normalize(old)
        if new == old:
            continue
        if "//" in new:
            to_fix_dfc.append((old, new))
        else:
            to_fix_partners.append((old, new))

    to_fix = to_fix_dfc + to_fix_partners

    print(f"{len(commanders_amp)} commandants avec '&' trouves dans deck_cards.")
    print(f"  {len(to_fix_dfc)} DFC a convertir '&' -> '//'")
    print(f"  {len(to_fix_partners)} partners a reordonner alphabetiquement\n")

    if to_fix_dfc:
        print("DFC :")
        for old, new in to_fix_dfc:
            print(f"  {old!r}\n  -> {new!r}")

    if to_fix_partners:
        print("\nPartners (reordonnement) :")
        for old, new in to_fix_partners:
            print(f"  {old!r}\n  -> {new!r}")

    if not to_fix:
        print("Rien a migrer.")
        conn.close()
        return

    if yes:
        print("\nAppliquer la migration ? [o/N] o (--yes)")
    else:
        confirm = input("\nAppliquer la migration ? [o/N] ").strip().lower()
        if confirm != "o":
            print("Annule.")
            conn.close()
            return

    # ── 2. Mettre a jour deck_cards.commander ────────────────────────────────
    dc_total = 0
    for old, new in to_fix:
        cur.execute("UPDATE deck_cards SET commander = %s WHERE commander = %s", (new, old))
        dc_total += cur.rowcount

    print(f"\ndeck_cards : {dc_total:,} lignes mises a jour.")

    # ── 3. Mettre a jour commanders.name ────────────────────────────────────
    cmd_total = 0
    for old, new in to_fix:
        cur.execute("SELECT 1 FROM commanders WHERE name = %s", (new,))
        already_exists = cur.fetchone() is not None
        if already_exists:
            cur.execute("DELETE FROM commanders WHERE name = %s", (old,))
        else:
            cur.execute("UPDATE commanders SET name = %s WHERE name = %s", (new, old))
        cmd_total += 1

    print(f"commanders  : {cmd_total} entrees mises a jour.")

    conn.commit()
    print("\nMigration terminee et commitee.")
    conn.close()


if __name__ == "__main__":
    main()
