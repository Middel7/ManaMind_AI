#!/usr/bin/env python3
"""
Recommande des cartes à ajouter ou retirer d'une decklist en interrogeant
deck_stat_commander (PostgreSQL).

Usage:
    python src/manamind/recommandation_populaire.py \
        --input example_Eluge_decklist.txt \
        --output recommendations_example_Eluge_decklist.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from pathlib import Path

LINE_PATTERN = re.compile(r"^(\d+)\s+(.*)$")
BASIC_LANDS = {"Island", "Plains", "Swamp", "Mountain", "Forest", "Wastes"}


def normalize_name(name: str) -> str:
    name = name.strip()
    if name.startswith("A-"):
        return name[2:].strip()
    return name


def parse_decklist_text(path: Path) -> tuple[dict[str, int], str | None]:
    """Parse un fichier de decklist (Arena / MTGO / texte brut).

    Retourne (cartes, commandant). Le commandant est détecté via la dernière
    section séparée par une ligne vide (zone commander Arena / MTGO).
    Les partners sont joints par ' // ' pour correspondre au format stocké en base.
    """
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = path.read_text(encoding="latin-1", errors="replace")

    lines = [line.strip() for line in text.splitlines()]
    sections: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if not line:
            if current:
                sections.append(current)
                current = []
            continue
        current.append(line)
    if current:
        sections.append(current)

    commander: str | None = None
    if len(sections) >= 2 and len(sections[-1]) in (1, 2):
        cmd_parts = []
        for line in sections[-1]:
            m = LINE_PATTERN.match(line)
            if m:
                cmd_parts.append(normalize_name(m.group(2)))
        if cmd_parts:
            if len(cmd_parts) == 2:
                # Appliquer la même heuristique que le parser scraper :
                # même premier mot → DFC (//) sinon partners (&)
                w0 = cmd_parts[0].split()[0].rstrip(",")
                w1 = cmd_parts[1].split()[0].rstrip(",")
                sep = " // " if w0 == w1 else " & "
                commander = sep.join(cmd_parts)
            else:
                commander = cmd_parts[0]

    cards: dict[str, int] = {}
    for line in lines:
        if not line:
            continue
        match = LINE_PATTERN.match(line)
        if not match:
            continue
        qty = int(match.group(1))
        card_name = normalize_name(match.group(2))
        if not card_name:
            continue
        cards[card_name] = cards.get(card_name, 0) + qty
    return cards, commander


def _query_stats(commander: str) -> list:
    """Interroge deck_stat_commander pour un commandant donné.

    Retourne des rows (card_name, decks_with_card, total_decks, inclusion_rate)
    triées par inclusion_rate DESC. Retourne [] en cas d'erreur ou de données absentes.
    """
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return []
    try:
        import sqlalchemy as sa
        engine = sa.create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text("""
                    SELECT card_name, decks_with_card, total_decks, inclusion_rate
                    FROM deck_stat_commander
                    WHERE LOWER(TRIM(commander)) = LOWER(TRIM(:cmd))
                    ORDER BY inclusion_rate DESC
                """),
                {"cmd": commander},
            ).fetchall()
        engine.dispose()
        return rows
    except Exception as exc:
        print(f"Avertissement DB: {exc}")
        return []


def recommend_from_db(
    cards: dict[str, int],
    commander: str | None,
    limit: int = 20,
) -> tuple[list[tuple[str, int, float]], list[tuple[str, int, float]]]:
    """Retourne (additions, removals) depuis deck_stat_commander.

    additions : cartes absentes du deck, inclusion_rate la plus haute.
    removals  : cartes présentes dans le deck, inclusion_rate la plus basse
                (candidates à retirer car peu populaires dans ce style de jeu).

    Chaque entrée = (card_name, decks_with_card, inclusion_rate).
    """
    if not commander:
        return [], []

    rows = _query_stats(commander)

    if not rows:
        return [], []

    deck_card_set = {normalize_name(c) for c in cards}
    additions: list[tuple[str, int, float]] = []
    removals: list[tuple[str, int, float]] = []

    for row in rows:
        card = row.card_name
        if card in BASIC_LANDS:
            continue
        cnt = row.decks_with_card or 0
        rate = float(row.inclusion_rate or 0.0)
        if card in deck_card_set:
            removals.append((card, cnt, rate))
        else:
            additions.append((card, cnt, rate))

    # Additions : déjà triées par inclusion_rate DESC (ORDER BY dans la requête)
    # Removals  : inclusion_rate ASC — les cartes les moins populaires en tête
    removals.sort(key=lambda x: x[2])

    return additions[:limit], removals[:limit]


def save_recommendations(
    output_path: Path,
    commander: str | None,
    additions: list[tuple[str, int, float]],
    removals: list[tuple[str, int, float]],
) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Section", "Card Name", "Score", "Support", "Deck Frequency"])
        writer.writerow(["Commander", commander or "unknown", "", "", ""])
        writer.writerow([])
        writer.writerow(["Additions", "", "", "", ""])
        for card, cnt, _rate in additions:
            writer.writerow(["add", card, cnt, "", ""])
        writer.writerow([])
        writer.writerow(["Removals", "", "", "", ""])
        for card, cnt, rate in removals:
            writer.writerow(["remove", card, "", cnt, round(rate, 1)])


def main() -> None:
    parser = argparse.ArgumentParser(description="Recommandations par popularité depuis deck_stat_commander.")
    parser.add_argument("--input",  required=True,                  help="Decklist texte (.txt)")
    parser.add_argument("--output", default="recommendations.csv",  help="CSV de sortie")
    args = parser.parse_args()

    cards, commander = parse_decklist_text(Path(args.input))
    print(f"Commander détecté : {commander or '(aucun)'}")

    additions, removals = recommend_from_db(cards, commander)
    print(f"{len(additions)} cartes à ajouter, {len(removals)} cartes à retirer suggérées")

    save_recommendations(Path(args.output), commander, additions, removals)
    print(f"Recommandations enregistrées dans {args.output}")


if __name__ == "__main__":
    main()
