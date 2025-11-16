#!/usr/bin/env python3
"""
Script d'exemple pour tester les fonctions KPI sur le dataset ManaMind.

Usage:
    PYTHONPATH=src uv run examples/test_kpi.py
"""

import logging
from pathlib import Path

from manamind.dataset import Dataset
from manamind.kpi import (
    analyze_card_density,
    analyze_card_uniqueness,
    analyze_commander_overlap,
    analyze_deck_type_distribution,
    compute_cumulative_frequency,
    generate_dataset_report,
    get_most_frequent_cards,
    get_most_frequent_cards_by_commander,
    plot_cumulative_frequency,
    plot_top_cards,
)

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Test les fonctionnalités KPI du module."""
    logger.info("=== Test ManaMind KPI Module ===\n")

    # Charger le dataset
    logger.info("Chargement du dataset...")
    data_dir = Path("data")
    dataset = Dataset(data_dir=data_dir)
    dataset.load_all_decks()

    logger.info(
        f"\nDataset chargé: {len(dataset.decks)} decks, {len(dataset.commanders)} commandants"
    )

    # Test 1: Distribution des types de decks
    logger.info("\n" + "=" * 80)
    logger.info("TEST 1: Distribution des types de decks")
    logger.info("=" * 80)
    deck_type_dist = analyze_deck_type_distribution(dataset.decks)
    logger.info(f"\n{deck_type_dist}")

    # Test 2: Top 25 cartes globales
    logger.info("\n" + "=" * 80)
    logger.info("TEST 2: Top 25 cartes les plus fréquentes (global)")
    logger.info("=" * 80)
    top_25_global = get_most_frequent_cards(dataset.decks, top_n=25)
    logger.info(f"\n{top_25_global}")

    # Test 3: Top 25 par commandant
    logger.info("\n" + "=" * 80)
    logger.info("TEST 3: Top 25 cartes par commandant")
    logger.info("=" * 80)
    top_25_by_commander = get_most_frequent_cards_by_commander(dataset, top_n=25)

    for commander, df in top_25_by_commander.items():
        logger.info(f"\n--- {commander} ---")
        logger.info(f"\n{df[['card_name', 'deck_count', 'deck_percentage']].head(10)}")

    # Test 4: Analyse de densité
    logger.info("\n" + "=" * 80)
    logger.info("TEST 4: Analyse de densité des cartes")
    logger.info("=" * 80)
    density_df = analyze_card_density(dataset.decks, min_deck_count=10)
    logger.info(f"\n{density_df.head(20)}")

    # Test 5: Fréquence cumulée
    logger.info("\n" + "=" * 80)
    logger.info("TEST 5: Courbe de fréquence cumulée")
    logger.info("=" * 80)
    cumulative_freq = compute_cumulative_frequency(dataset.decks)
    logger.info(f"\nPremières lignes:\n{cumulative_freq.head(20)}")
    logger.info(f"\nCarte au rang 50:\n{cumulative_freq.iloc[49]}")
    logger.info(f"\nCarte au rang 100:\n{cumulative_freq.iloc[99]}")

    # Test 6: Analyse d'unicité
    logger.info("\n" + "=" * 80)
    logger.info("TEST 6: Analyse d'unicité des cartes")
    logger.info("=" * 80)
    uniqueness_df = analyze_card_uniqueness(dataset)
    logger.info("\nDistribution par catégorie:")
    summary = uniqueness_df.groupby("category").size().reset_index(name="count")
    summary["percentage"] = (summary["count"] / len(uniqueness_df)) * 100
    logger.info(f"\n{summary}")

    # Test 7: Overlap entre commandants
    logger.info("\n" + "=" * 80)
    logger.info("TEST 7: Cartes partagées entre commandants")
    logger.info("=" * 80)
    overlap_df = analyze_commander_overlap(dataset)
    logger.info("\nCartes universelles (tous les commandants):")
    universal = overlap_df[overlap_df["is_universal"]]
    logger.info(f"\n{universal[['card_name', 'commander_count']]}")

    logger.info("\nCartes spécifiques à un seul commandant:")
    specific = overlap_df[overlap_df["commander_count"] == 1]
    logger.info(f"Nombre de cartes spécifiques: {len(specific)}")

    # Test 8: Visualisations
    logger.info("\n" + "=" * 80)
    logger.info("TEST 8: Génération de visualisations")
    logger.info("=" * 80)

    try:
        logger.info("Création du graphique Top 25...")
        fig1 = plot_top_cards(
            top_25_global,
            title="Top 25 Most Frequent Cards (Global)",
        )
        fig1.savefig("outputs/top_25_cards.png", dpi=150, bbox_inches="tight")
        logger.info("✓ Sauvegardé: outputs/top_25_cards.png")

        logger.info("Création de la courbe de fréquence cumulée...")
        fig2 = plot_cumulative_frequency(cumulative_freq)
        fig2.savefig("outputs/cumulative_frequency.png", dpi=150, bbox_inches="tight")
        logger.info("✓ Sauvegardé: outputs/cumulative_frequency.png")

    except Exception as e:
        logger.error(f"Erreur lors de la génération des visualisations: {e}")

    # Test 9: Rapport complet
    logger.info("\n" + "=" * 80)
    logger.info("TEST 9: Génération du rapport complet")
    logger.info("=" * 80)
    report = generate_dataset_report(dataset)
    logger.info("\nRapport complet généré avec succès!")
    logger.info(f"Sections du rapport: {list(report.keys())}")

    logger.info("\n=== Tests KPI terminés ===")


if __name__ == "__main__":
    # Créer le dossier outputs s'il n'existe pas
    Path("outputs").mkdir(exist_ok=True)
    main()
