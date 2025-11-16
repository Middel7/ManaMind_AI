"""
KPI module for ManaMind AI statistical analysis.

This module provides functions to compute statistics and KPIs on:
- Card: Individual card statistics
- Deck: Deck-level analysis
- Dataset: Global dataset analysis

Key analyses include:
- Card distribution within decks
- Most frequent cards by commander
- Density analysis (card repetition across decks)
- Top 25 cards per commander
- Cumulative frequency curves
- Deck composition patterns
"""

import logging
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt

from manamind.dataset import Dataset, Deck

logger = logging.getLogger(__name__)


# ==================== CARD STATISTICS ====================


def get_card_frequency_in_deck(deck: Deck) -> dict[str, int]:
    """
    Get card frequency distribution for a single deck.

    Args:
        deck (Deck): The deck to analyze

    Returns:
        Dict[str, int]: Mapping of card name to quantity
    """
    return {card.name: card.quantity for card in deck.cards}


def get_unique_cards_in_deck(deck: Deck, exclude_commander: bool = True) -> int:
    """
    Count unique cards in a deck.

    Args:
        deck (Deck): The deck to analyze
        exclude_commander (bool): Whether to exclude commanders from count

    Returns:
        int: Number of unique cards
    """
    if exclude_commander:
        return len(deck.non_commanders)
    return len(deck.cards)


def get_commander_cards_stats(deck: Deck) -> dict[str, any]:
    """
    Get statistics about commander cards in a deck.

    Args:
        deck (Deck): The deck to analyze

    Returns:
        Dict: Commander statistics (count, names, types)
    """
    commanders = deck.commanders
    return {
        "commander_count": len(commanders),
        "commander_names": [c.name for c in commanders],
        "is_partner": len(commanders) == 2,
        "total_commander_quantity": sum(c.quantity for c in commanders),
    }


# ==================== DECK STATISTICS ====================


def get_deck_composition_stats(deck: Deck) -> dict[str, any]:
    """
    Get comprehensive deck composition statistics.

    Args:
        deck (Deck): The deck to analyze

    Returns:
        Dict: Deck composition stats
    """
    non_commanders = deck.non_commanders
    commanders = deck.commanders

    return {
        "deck_id": deck.id,
        "commander_name": deck.commander_name,
        "deck_type": deck.deck_type.value,
        "total_cards": sum(c.quantity for c in deck.cards),
        "unique_cards": len(deck.cards),
        "unique_non_commander_cards": len(non_commanders),
        "commander_count": len(commanders),
        "avg_card_quantity": np.mean([c.quantity for c in deck.cards]),
        "max_card_quantity": max([c.quantity for c in deck.cards]),
        "cards_with_quantity_1": sum(1 for c in deck.cards if c.quantity == 1),
    }


def analyze_deck_type_distribution(decks: list[Deck]) -> pd.DataFrame:
    """
    Analyze distribution of deck types.

    Args:
        decks (List[Deck]): List of decks to analyze

    Returns:
        pd.DataFrame: Deck type distribution statistics
    """
    logger.info(f"Analyzing deck type distribution for {len(decks)} decks")

    type_counts = Counter(deck.deck_type.value for deck in decks)

    df = pd.DataFrame(
        [
            {
                "deck_type": deck_type,
                "count": count,
                "percentage": (count / len(decks)) * 100,
            }
            for deck_type, count in type_counts.items()
        ]
    )

    return df.sort_values("count", ascending=False).reset_index(drop=True)


# ==================== DATASET STATISTICS ====================


def get_most_frequent_cards(
    decks: list[Deck],
    top_n: int = 25,
    exclude_commanders: bool = True,
) -> pd.DataFrame:
    """
    Get the most frequent cards across all decks.

    Args:
        decks (List[Deck]): List of decks to analyze
        top_n (int): Number of top cards to return
        exclude_commanders (bool): Whether to exclude commander cards

    Returns:
        pd.DataFrame: Top N most frequent cards with statistics
    """
    logger.info(f"Computing top {top_n} most frequent cards across {len(decks)} decks")

    card_counter: Counter = Counter()
    card_deck_count: Counter = Counter()  # Number of decks containing each card

    for deck in decks:
        cards_to_analyze = deck.non_commanders if exclude_commanders else deck.cards
        deck_cards_seen = set()

        for card in cards_to_analyze:
            card_counter[card.name] += card.quantity
            if card.name not in deck_cards_seen:
                card_deck_count[card.name] += 1
                deck_cards_seen.add(card.name)

    # Build DataFrame
    data = []
    for card_name, total_quantity in card_counter.most_common(top_n):
        data.append(
            {
                "card_name": card_name,
                "total_quantity": total_quantity,
                "deck_count": card_deck_count[card_name],
                "deck_percentage": (card_deck_count[card_name] / len(decks)) * 100,
                "avg_quantity_per_deck": total_quantity / len(decks),
            }
        )

    df = pd.DataFrame(data)
    top_card = df.iloc[0]
    logger.info(
        f"Top card: {top_card['card_name']} appears in "
        f"{top_card['deck_percentage']:.1f}% of decks"
    )

    return df


def get_most_frequent_cards_by_commander(
    dataset: Dataset,
    top_n: int = 25,
    exclude_commanders: bool = True,
) -> dict[str, pd.DataFrame]:
    """
    Get most frequent cards for each commander separately.

    Args:
        dataset (Dataset): The dataset to analyze
        top_n (int): Number of top cards per commander
        exclude_commanders (bool): Whether to exclude commander cards

    Returns:
        Dict[str, pd.DataFrame]: Mapping of commander name to their top cards DataFrame
    """
    logger.info(f"Computing top {top_n} cards by commander")

    results = {}
    for commander_name, decks in dataset.commanders.items():
        logger.info(f"Analyzing {commander_name}: {len(decks)} decks")
        results[commander_name] = get_most_frequent_cards(
            decks, top_n=top_n, exclude_commanders=exclude_commanders
        )

    return results


def analyze_card_density(
    decks: list[Deck],
    min_deck_count: int = 2,
    exclude_commanders: bool = True,
) -> pd.DataFrame:
    """
    Analyze card density: how often the same cards appear across different decks.

    Higher density = same cards appear frequently across many decks
    Lower density = high diversity, different cards in different decks

    Args:
        decks (List[Deck]): List of decks to analyze
        min_deck_count (int): Minimum number of decks a card must appear in
        exclude_commanders (bool): Whether to exclude commander cards

    Returns:
        pd.DataFrame: Card density analysis
    """
    logger.info(
        f"Analyzing card density across {len(decks)} decks (min_deck_count={min_deck_count})"
    )

    card_deck_presence: dict[str, list[str]] = defaultdict(list)

    for deck in decks:
        cards_to_analyze = deck.non_commanders if exclude_commanders else deck.cards
        for card in cards_to_analyze:
            card_deck_presence[card.name].append(deck.id)

    # Filter cards by minimum deck count
    filtered_cards = {
        card_name: deck_ids
        for card_name, deck_ids in card_deck_presence.items()
        if len(deck_ids) >= min_deck_count
    }

    data = []
    for card_name, deck_ids in filtered_cards.items():
        data.append(
            {
                "card_name": card_name,
                "deck_count": len(deck_ids),
                "deck_percentage": (len(deck_ids) / len(decks)) * 100,
                "density_score": len(deck_ids) / len(decks),  # 0 to 1
            }
        )

    df = pd.DataFrame(data)
    df = df.sort_values("deck_count", ascending=False).reset_index(drop=True)

    logger.info(f"Found {len(df)} cards appearing in at least {min_deck_count} decks")
    if not df.empty:
        logger.info(
            f"Highest density card: {df.iloc[0]['card_name']} "
            f"({df.iloc[0]['deck_percentage']:.1f}% of decks)"
        )

    return df


def compute_cumulative_frequency(
    decks: list[Deck],
    exclude_commanders: bool = True,
) -> pd.DataFrame:
    """
    Compute cumulative frequency curve for cards.

    Shows how many cards account for X% of total card appearances.

    Args:
        decks (List[Deck]): List of decks to analyze
        exclude_commanders (bool): Whether to exclude commander cards

    Returns:
        pd.DataFrame: Cumulative frequency data
    """
    logger.info("Computing cumulative frequency distribution")

    # Count total card appearances
    card_counter: Counter = Counter()
    for deck in decks:
        cards_to_analyze = deck.non_commanders if exclude_commanders else deck.cards
        for card in cards_to_analyze:
            card_counter[card.name] += card.quantity

    # Sort by frequency (descending)
    sorted_cards = card_counter.most_common()
    total_appearances = sum(card_counter.values())

    # Compute cumulative percentages
    cumulative_data = []
    cumulative_sum = 0

    for rank, (card_name, count) in enumerate(sorted_cards, start=1):
        cumulative_sum += count
        cumulative_percentage = (cumulative_sum / total_appearances) * 100

        cumulative_data.append(
            {
                "rank": rank,
                "card_name": card_name,
                "count": count,
                "percentage": (count / total_appearances) * 100,
                "cumulative_count": cumulative_sum,
                "cumulative_percentage": cumulative_percentage,
            }
        )

    df = pd.DataFrame(cumulative_data)

    # Log insights
    top_10_pct = df[df["cumulative_percentage"] <= 10]
    top_50_pct = df[df["cumulative_percentage"] <= 50]

    logger.info(f"Total unique cards: {len(df)}")
    logger.info(f"Top {len(top_10_pct)} cards account for 10% of all appearances")
    logger.info(f"Top {len(top_50_pct)} cards account for 50% of all appearances")

    return df


def analyze_card_uniqueness(dataset: Dataset) -> pd.DataFrame:
    """
    Analyze card uniqueness: cards that appear in only one deck vs. many decks.

    Args:
        dataset (Dataset): The dataset to analyze

    Returns:
        pd.DataFrame: Card uniqueness distribution
    """
    logger.info("Analyzing card uniqueness across dataset")

    card_deck_count: Counter = Counter()

    for deck in dataset.decks:
        seen_in_deck = set()
        for card in deck.non_commanders:
            if card.name not in seen_in_deck:
                card_deck_count[card.name] += 1
                seen_in_deck.add(card.name)

    # Categorize cards
    uniqueness_data = []
    for card_name, deck_count in card_deck_count.items():
        if deck_count == 1:
            category = "Unique (1 deck)"
        elif deck_count <= 5:
            category = "Rare (2-5 decks)"
        elif deck_count <= 20:
            category = "Uncommon (6-20 decks)"
        elif deck_count <= 50:
            category = "Common (21-50 decks)"
        else:
            category = "Staple (50+ decks)"

        uniqueness_data.append(
            {
                "card_name": card_name,
                "deck_count": deck_count,
                "category": category,
            }
        )

    df = pd.DataFrame(uniqueness_data)

    # Summary statistics
    summary = df.groupby("category").size().reset_index(name="card_count")
    summary["percentage"] = (summary["card_count"] / len(df)) * 100

    logger.info("\nCard uniqueness distribution:")
    for _, row in summary.iterrows():
        logger.info(f"  {row['category']}: {row['card_count']} cards ({row['percentage']:.1f}%)")

    return df


def analyze_commander_overlap(dataset: Dataset) -> pd.DataFrame:
    """
    Analyze card overlap between different commanders.

    Identifies cards that appear frequently across multiple commanders vs.
    commander-specific cards.

    Args:
        dataset (Dataset): The dataset to analyze

    Returns:
        pd.DataFrame: Commander overlap analysis
    """
    logger.info("Analyzing card overlap between commanders")

    # Track which commanders use which cards
    card_to_commanders: dict[str, set] = defaultdict(set)

    for commander_name, decks in dataset.commanders.items():
        for deck in decks:
            for card in deck.non_commanders:
                card_to_commanders[card.name].add(commander_name)

    # Build analysis
    data = []
    for card_name, commanders_set in card_to_commanders.items():
        data.append(
            {
                "card_name": card_name,
                "commander_count": len(commanders_set),
                "commander_names": ", ".join(sorted(commanders_set)),
                "is_universal": len(commanders_set) == len(dataset.commanders),
            }
        )

    df = pd.DataFrame(data)
    df = df.sort_values("commander_count", ascending=False).reset_index(drop=True)

    # Log insights
    universal_cards = df[df["is_universal"]]
    num_commanders = len(dataset.commanders)
    logger.info(
        f"Found {len(universal_cards)} universal cards "
        f"(appear in all {num_commanders} commanders)"
    )

    return df


def get_deck_similarity_matrix(decks: list[Deck]) -> pd.DataFrame:
    """
    Compute Jaccard similarity matrix between decks based on card overlap.

    Jaccard similarity = |A ∩ B| / |A ∪ B|

    Args:
        decks (List[Deck]): List of decks to compare

    Returns:
        pd.DataFrame: Similarity matrix (deck_id x deck_id)
    """
    logger.info(f"Computing similarity matrix for {len(decks)} decks")

    # Extract card sets for each deck (excluding commanders)
    deck_card_sets = {deck.id: set(card.name for card in deck.non_commanders) for deck in decks}

    # Compute pairwise Jaccard similarity
    deck_ids = [deck.id for deck in decks]
    n = len(deck_ids)
    similarity_matrix = np.zeros((n, n))

    for i, deck_id_1 in enumerate(deck_ids):
        for j, deck_id_2 in enumerate(deck_ids):
            if i == j:
                similarity_matrix[i, j] = 1.0
            elif i < j:
                set1 = deck_card_sets[deck_id_1]
                set2 = deck_card_sets[deck_id_2]
                intersection = len(set1 & set2)
                union = len(set1 | set2)
                jaccard = intersection / union if union > 0 else 0.0
                similarity_matrix[i, j] = jaccard
                similarity_matrix[j, i] = jaccard  # Symmetric

    df = pd.DataFrame(similarity_matrix, index=deck_ids, columns=deck_ids)

    # Log statistics
    upper_triangle = similarity_matrix[np.triu_indices(n, k=1)]
    logger.info(f"Average deck similarity: {upper_triangle.mean():.3f}")
    logger.info(f"Max deck similarity: {upper_triangle.max():.3f}")
    logger.info(f"Min deck similarity: {upper_triangle.min():.3f}")

    return df


# ==================== VISUALIZATION FUNCTIONS ====================


def plot_top_cards(
    df: pd.DataFrame,
    title: str = "Top 25 Most Frequent Cards",
    figsize: tuple[int, int] = (12, 8),
) -> plt.Figure:
    """
    Plot horizontal bar chart of top cards.

    Args:
        df (pd.DataFrame): DataFrame from get_most_frequent_cards()
        title (str): Plot title
        figsize (Tuple[int, int]): Figure size

    Returns:
        plt.Figure: Matplotlib figure
    """
    fig, ax = plt.subplots(figsize=figsize)

    # Sort by deck_count
    df_sorted = df.sort_values("deck_count", ascending=True)

    ax.barh(df_sorted["card_name"], df_sorted["deck_count"], color="steelblue")
    ax.set_xlabel("Number of Decks")
    ax.set_ylabel("Card Name")
    ax.set_title(title)
    ax.grid(axis="x", alpha=0.3)

    plt.tight_layout()
    return fig


def plot_cumulative_frequency(
    df: pd.DataFrame,
    figsize: tuple[int, int] = (12, 6),
) -> plt.Figure:
    """
    Plot cumulative frequency curve.

    Args:
        df (pd.DataFrame): DataFrame from compute_cumulative_frequency()
        figsize (Tuple[int, int]): Figure size

    Returns:
        plt.Figure: Matplotlib figure
    """
    fig, ax = plt.subplots(figsize=figsize)

    ax.plot(df["rank"], df["cumulative_percentage"], linewidth=2, color="darkblue")
    ax.axhline(y=50, color="red", linestyle="--", alpha=0.5, label="50% threshold")
    ax.axhline(y=80, color="orange", linestyle="--", alpha=0.5, label="80% threshold")

    ax.set_xlabel("Card Rank (by frequency)")
    ax.set_ylabel("Cumulative Percentage (%)")
    ax.set_title("Cumulative Frequency Distribution of Cards")
    ax.grid(alpha=0.3)
    ax.legend()

    plt.tight_layout()
    return fig


def plot_card_density_distribution(
    df: pd.DataFrame,
    bins: int = 50,
    figsize: tuple[int, int] = (12, 6),
) -> plt.Figure:
    """
    Plot card density distribution histogram.

    Args:
        df (pd.DataFrame): DataFrame from analyze_card_density()
        bins (int): Number of histogram bins
        figsize (Tuple[int, int]): Figure size

    Returns:
        plt.Figure: Matplotlib figure
    """
    fig, ax = plt.subplots(figsize=figsize)

    ax.hist(df["deck_percentage"], bins=bins, color="seagreen", alpha=0.7, edgecolor="black")
    ax.set_xlabel("Deck Percentage (%)")
    ax.set_ylabel("Number of Cards")
    ax.set_title("Card Density Distribution")
    ax.grid(alpha=0.3)

    plt.tight_layout()
    return fig


def plot_deck_type_distribution(
    df: pd.DataFrame,
    figsize: tuple[int, int] = (10, 6),
) -> plt.Figure:
    """
    Plot deck type distribution as pie chart.

    Args:
        df (pd.DataFrame): DataFrame from analyze_deck_type_distribution()
        figsize (Tuple[int, int]): Figure size

    Returns:
        plt.Figure: Matplotlib figure
    """
    fig, ax = plt.subplots(figsize=figsize)

    ax.pie(
        df["count"],
        labels=df["deck_type"].apply(lambda x: x if x else "Casual"),
        autopct="%1.1f%%",
        startangle=90,
        colors=["#ff9999", "#66b3ff", "#99ff99"],
    )
    ax.set_title("Deck Type Distribution")

    plt.tight_layout()
    return fig


# ==================== COMPREHENSIVE REPORT ====================


def generate_dataset_report(dataset: Dataset, output_path: str | None = None) -> dict[str, any]:
    """
    Generate comprehensive statistical report for the dataset.

    Args:
        dataset (Dataset): The dataset to analyze
        output_path (Optional[str]): Path to save report (if provided)

    Returns:
        Dict: Comprehensive report with all statistics
    """
    logger.info("=" * 80)
    logger.info("GENERATING COMPREHENSIVE DATASET REPORT")
    logger.info("=" * 80)

    report = {}

    # 1. Basic statistics
    logger.info("\n1. BASIC STATISTICS")
    report["basic_stats"] = {
        "total_decks": len(dataset.decks),
        "total_commanders": len(dataset.commanders),
        "commander_names": list(dataset.commanders.keys()),
        "decks_per_commander": {cmd: len(decks) for cmd, decks in dataset.commanders.items()},
    }
    logger.info(f"Total decks: {report['basic_stats']['total_decks']}")
    logger.info(f"Total commanders: {report['basic_stats']['total_commanders']}")

    # 2. Deck type distribution
    logger.info("\n2. DECK TYPE DISTRIBUTION")
    report["deck_type_distribution"] = analyze_deck_type_distribution(dataset.decks)
    logger.info(f"\n{report['deck_type_distribution']}")

    # 3. Top 25 cards globally
    logger.info("\n3. TOP 25 MOST FREQUENT CARDS (GLOBAL)")
    report["top_25_global"] = get_most_frequent_cards(dataset.decks, top_n=25)
    logger.info(
        f"\n{report['top_25_global'][['card_name', 'deck_count', 'deck_percentage']].head(10)}"
    )

    # 4. Top 25 cards by commander
    logger.info("\n4. TOP 25 CARDS BY COMMANDER")
    report["top_25_by_commander"] = get_most_frequent_cards_by_commander(dataset, top_n=25)
    for commander, df in report["top_25_by_commander"].items():
        logger.info(f"\n{commander} - Top 5:")
        logger.info(f"\n{df[['card_name', 'deck_count', 'deck_percentage']].head(5)}")

    # 5. Card density analysis
    logger.info("\n5. CARD DENSITY ANALYSIS")
    report["card_density"] = analyze_card_density(dataset.decks, min_deck_count=2)
    logger.info(
        f"\n{report['card_density'][['card_name', 'deck_count', 'deck_percentage']].head(10)}"
    )

    # 6. Cumulative frequency
    logger.info("\n6. CUMULATIVE FREQUENCY ANALYSIS")
    report["cumulative_frequency"] = compute_cumulative_frequency(dataset.decks)

    # 7. Card uniqueness
    logger.info("\n7. CARD UNIQUENESS ANALYSIS")
    report["card_uniqueness"] = analyze_card_uniqueness(dataset)

    # 8. Commander overlap
    logger.info("\n8. COMMANDER OVERLAP ANALYSIS")
    report["commander_overlap"] = analyze_commander_overlap(dataset)
    universal_cards = report["commander_overlap"][report["commander_overlap"]["is_universal"]]
    logger.info(f"\nUniversal cards ({len(universal_cards)}):")
    logger.info(f"\n{universal_cards['card_name'].tolist()}")

    logger.info("\n" + "=" * 80)
    logger.info("REPORT GENERATION COMPLETE")
    logger.info("=" * 80)

    # Save to file if requested
    if output_path:
        logger.info(f"\nSaving report to {output_path}")
        # TODO: Implement saving logic (pickle, JSON, or Excel)

    return report
