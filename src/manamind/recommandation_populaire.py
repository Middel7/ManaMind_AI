#!/usr/bin/env python3
"""
Recommend cards to add or remove from a decklist using the dataset in data/Decklists.

Usage:
    python src/manamind/recommandation_populaire.py \
        --input example_Eluge_decklist.txt \
        --output recommendations_example_Eluge_decklist.csv
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

DECKLISTS_ROOT = Path(__file__).resolve().parents[2] / "data" / "Decklists"

LINE_PATTERN = re.compile(r"^(\d+)\s+(.*)$")

BASIC_LANDS = {"Island", "Plains", "Swamp", "Mountain", "Forest", "Wastes"}


@dataclass
class DeckInfo:
    cards: set[str]
    commander: str | None


def normalize_name(name: str) -> str:
    name = name.strip()
    if name.startswith("A-"):
        return name[2:].strip()
    return name


def parse_decklist_text(path: Path) -> tuple[dict[str, int], str | None]:
    # Essaie UTF-8 (avec BOM), puis cp1252 (Windows), puis latin-1 en fallback
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
    if len(sections) >= 2 and len(sections[-1]) == 1:
        match = LINE_PATTERN.match(sections[-1][0])
        if match:
            commander = normalize_name(match.group(2))

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


def load_deck_dataset(root: Path) -> list[DeckInfo]:
    decks: list[DeckInfo] = []
    for deck_file in sorted(root.rglob("*.csv")):
        try:
            with deck_file.open("r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, delimiter=";")
                cards: set[str] = set()
                commander: str | None = None
                for row in reader:
                    raw_name = row.get("Card Name", "").strip()
                    if not raw_name:
                        continue
                    card_name = normalize_name(raw_name)
                    cards.add(card_name)
                    if row.get("Commander", "").strip().upper() == "YES":
                        commander = card_name
                if cards:
                    decks.append(DeckInfo(cards=cards, commander=commander))
        except Exception:
            continue
    return decks


def build_statistics(decks: list[DeckInfo]) -> tuple[Counter, dict[str, list[DeckInfo]], list[DeckInfo]]:
    deck_frequency = Counter()
    commander_decks: dict[str, list[DeckInfo]] = defaultdict(list)

    for deck in decks:
        for card in deck.cards:
            deck_frequency[card] += 1
        if deck.commander:
            commander_decks[deck.commander].append(deck)

    # La co-occurrence n'est plus pré-calculée globalement (trop coûteux sur 28k decks).
    # On retourne la liste brute pour permettre un calcul local si besoin.
    return deck_frequency, commander_decks, decks


def _build_cooccurrence_for(input_cards: set[str], decks: list[DeckInfo]) -> dict[str, Counter]:
    """Calcule la co-occurrence uniquement pour les cartes du deck cible."""
    cooccurrence: dict[str, Counter] = defaultdict(Counter)
    for deck in decks:
        relevant = deck.cards & input_cards
        if not relevant:
            continue
        for card in relevant:
            cooccurrence[card].update(deck.cards - {card})
    return cooccurrence


def recommend_additions(
    input_cards: set[str],
    all_cards: set[str],
    deck_frequency: Counter,
    commander: str | None,
    commander_decks: dict[str, list[DeckInfo]],
    all_decks: list[DeckInfo],
    limit: int = 20,
) -> list[tuple[str, int]]:
    candidates = all_cards - input_cards - BASIC_LANDS
    score = Counter()
    if commander and commander in commander_decks:
        for deck in commander_decks[commander]:
            for card in deck.cards:
                if card not in input_cards and card not in BASIC_LANDS:
                    score[card] += 1
        if len(score) >= limit:
            return score.most_common(limit)
        # Fallback co-occurrence calculée à la volée sur les cartes du deck uniquement
        cooccurrence = _build_cooccurrence_for(input_cards, all_decks)
        fallback = Counter()
        for card in input_cards:
            fallback.update(cooccurrence.get(card, Counter()))
        for card in input_cards:
            fallback.pop(card, None)
        for card, extra_score in fallback.items():
            if card in candidates:
                score[card] += extra_score
        return [(card, score[card]) for card in score.most_common(limit) if card in candidates][:limit]

    cooccurrence = _build_cooccurrence_for(input_cards, all_decks)
    for card in input_cards:
        score.update(cooccurrence.get(card, Counter()))
    for card in input_cards:
        score.pop(card, None)
    return [(card, score[card]) for card in score.most_common(limit) if card in candidates][:limit]


def recommend_removals(
    input_cards: set[str],
    deck_frequency: Counter,
    commander: str | None,
    commander_decks: dict[str, list[DeckInfo]],
    all_decks: list[DeckInfo],
    commander_card: str | None,
    limit: int = 20,
) -> list[tuple[str, int, float]]:
    removals: list[tuple[str, int, float]] = []
    candidates = input_cards - {commander_card} if commander_card else input_cards
    candidates -= BASIC_LANDS
    if commander and commander in commander_decks:
        for card in sorted(candidates):
            support = sum(1 for deck in commander_decks[commander] if card in deck.cards)
            removals.append((card, support, deck_frequency[card]))
        removals.sort(key=lambda item: (item[1], item[2], item[0]))
        return removals[:limit]

    cooccurrence = _build_cooccurrence_for(input_cards, all_decks)
    for card in sorted(candidates):
        support = sum(cooccurrence.get(card, Counter()).get(other, 0) for other in candidates if other != card)
        removals.append((card, support, deck_frequency[card]))
    removals.sort(key=lambda item: (item[1], item[2], item[0]))
    return removals[:limit]


def save_recommendations(
    output_path: Path,
    commander: str | None,
    additions: list[tuple[str, int]],
    removals: list[tuple[str, int, float]],
    deck_size: int,
) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Section", "Card Name", "Score", "Support", "Deck Frequency"])
        writer.writerow(["Commander", commander or "unknown", "", "", ""])
        writer.writerow([])
        writer.writerow(["Additions", "", "", "", ""])
        for card, score in additions:
            writer.writerow(["add", card, score, "", ""])
        writer.writerow([])
        writer.writerow(["Removals", "", "", "", ""])
        for card, support, frequency in removals:
            writer.writerow(["remove", card, "", support, frequency])


def print_recommendations(
    commander: str | None,
    additions: list[tuple[str, int]],
    removals: list[tuple[str, int, float]],
) -> None:
    print("Commander:", commander or "unknown")
    print("\nTop 20 cartes à ajouter:")
    for rank, (card, score) in enumerate(additions, start=1):
        print(f"{rank:2d}. {card} (score={score})")

    print("\nTop 20 cartes à retirer:")
    for rank, (card, support, frequency) in enumerate(removals, start=1):
        print(f"{rank:2d}. {card} (support={support}, freq={frequency})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Recommend cards to add/remove from a decklist.")
    parser.add_argument("--input", required=True, help="Path to the example decklist text file")
    parser.add_argument("--output", default="recommendations.csv", help="Path to save the recommendation CSV output")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    cards, commander = parse_decklist_text(input_path)
    deck_infos = load_deck_dataset(DECKLISTS_ROOT)
    deck_frequency, commander_decks, all_decks = build_statistics(deck_infos)

    all_cards = set(deck_frequency) | set(cards)
    additions = recommend_additions(set(cards), all_cards, deck_frequency, commander, commander_decks, all_decks)
    removals = recommend_removals(set(cards), deck_frequency, commander, commander_decks, all_decks, commander)

    print_recommendations(commander, additions, removals)
    save_recommendations(output_path, commander, additions, removals, len(cards))
    print(f"\nSaved recommendations to {output_path}")


if __name__ == "__main__":
    main()
