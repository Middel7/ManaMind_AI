#!/usr/bin/env python3
"""
Script de test pour valider les classes Card, Deck et Dataset.

Usage:
    uv run python examples/test_dataset.py
"""

import logging
from pathlib import Path

from manamind.dataset import Card, Dataset

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Test les fonctionnalités principales du module dataset."""
    logger.info("=== Test ManaMind Dataset Module ===\n")

    # Test 1: Créer une carte
    logger.info("Test 1: Création d'une carte")
    card = Card(name="Black Lotus", quantity=1, is_commander=False)
    logger.info(f"Card created: {card.name} (ID: {card.id})")

    # Test 2: Charger un dataset
    logger.info("\nTest 2: Chargement du dataset")
    data_dir = Path("data")
    dataset = Dataset(data_dir=data_dir)

    # Test 3: Charger un deck spécifique
    logger.info("\nTest 3: Chargement d'un deck Eluge, the Shoreless Sea")
    try:
        eluge_decks = dataset.load_commander_decks("Eluge_the_Shoreless_Sea")
        if eluge_decks:
            first_deck = eluge_decks[0]
            logger.info(f"Deck ID: {first_deck.id}")
            logger.info(f"Commander: {first_deck.commander_name}")
            logger.info(f"Deck Type: {first_deck.deck_type}")
            logger.info(f"Number of cards: {len(first_deck.cards)}")
            logger.info(f"Commanders: {', '.join(c.name for c in first_deck.commanders)}")
            logger.info(f"Total quantity: {sum(c.quantity for c in first_deck.cards)}")
    except Exception as e:
        logger.error(f"Failed to load Eluge decks: {e}")

    # Test 4: Charger tous les decks
    logger.info("\nTest 4: Chargement de tous les decks")
    try:
        dataset.load_all_decks()
        logger.info(f"Total decks loaded: {len(dataset.decks)}")
        logger.info(f"Commanders found: {len(dataset.commanders)}")
        for commander, decks in list(dataset.commanders.items())[:3]:
            logger.info(f"  - {commander}: {len(decks)} deck(s)")
    except Exception as e:
        logger.error(f"Failed to load all decks: {e}")

    # Test 5: Conversion en DataFrame
    if dataset.decks:
        logger.info("\nTest 5: Conversion en DataFrame")
        df = dataset.to_dataframe()
        logger.info(f"DataFrame shape: {df.shape}")
        logger.info(f"Columns: {list(df.columns)}")
        logger.info("\nFirst 5 rows:")
        logger.info(f"\n{df.head()}")

    # Test 6: Train/Test Split
    if len(dataset.decks) > 5:
        logger.info("\nTest 6: Train/Test Split")
        train_decks, test_decks = dataset.train_test_split(test_size=0.2, random_state=42)
        logger.info(f"Train set: {len(train_decks)} decks")
        logger.info(f"Test set: {len(test_decks)} decks")

    logger.info("\n=== Tests completed ===")


if __name__ == "__main__":
    main()
