"""
Dataset module for ManaMind AI to handle data loading and input validation.

This module provides structured representations for:
- Card: Individual Magic: The Gathering cards
- Deck: Commander deck with exactly 100 cards
- Dataset: Collection of decks for training and evaluation
"""

import logging
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class CommanderStatus(str, Enum):
    """Enum for commander status in deck."""

    YES = "YES"
    NO = "NO"


class DeckType(str, Enum):
    """Enum for deck type categorization."""

    CEDH = "CEDH"  # Competitive EDH
    BUDGET = "BUDGET"  # Budget deck (≤ $100)
    CASUAL = ""  # Casual EDH (default)


class Card(BaseModel):
    """
    Represents a Magic: The Gathering card.

    Attributes:
        id (UUID): Unique identifier for the card instance
        name (str): Card name in English with standardized casing
        quantity (int): Number of copies in deck (≥1)
        is_commander (bool): Whether this card is a commander
    """

    id: UUID = Field(default_factory=uuid4, description="Unique card instance identifier")
    name: str = Field(..., min_length=1, description="Card name in English")
    quantity: int = Field(..., ge=1, description="Number of copies in deck")
    is_commander: bool = Field(default=False, description="Commander status")

    @field_validator("name")
    @classmethod
    def validate_card_name(cls, v: str) -> str:
        """Validate and normalize card name."""
        if not v or not v.strip():
            raise ValueError("Card name cannot be empty")
        # Preserve original format including // for double-faced cards
        return v.strip()

    class Config:
        """Pydantic configuration."""

        frozen = False  # Allow modifications if needed


class Deck(BaseModel):
    """
    Represents a Commander/EDH deck with exactly 100 cards.

    A deck must have:
    - Exactly 100 cards (sum of all quantities)
    - At least 1 and at most 2 commanders (for partner commanders)
    - A unique deck ID extracted from CSV filename

    Attributes:
        id (str): Deck identifier from CSV filename (without .csv extension)
        commander_name (str): Name of the deck's commander(s)
        cards (List[Card]): List of Card instances in the deck
        deck_type (DeckType): Type of deck (CEDH, BUDGET, CASUAL)
        date_created (Optional[str]): Creation timestamp
        date_modified (Optional[str]): Last modification timestamp
    """

    id: str = Field(..., description="Deck ID from CSV filename")
    commander_name: str = Field(..., min_length=1, description="Commander name")
    cards: list[Card] = Field(default_factory=list, description="List of cards in deck")
    deck_type: DeckType = Field(default=DeckType.CASUAL, description="Deck type categorization")
    date_created: str | None = Field(None, description="Creation timestamp")
    date_modified: str | None = Field(None, description="Last modification timestamp")

    @field_validator("id")
    @classmethod
    def validate_deck_id(cls, v: str) -> str:
        """Validate deck ID format."""
        if not v or not v.strip():
            raise ValueError("Deck ID cannot be empty")
        return v.strip()

    @model_validator(mode="after")
    def validate_deck_composition(self) -> "Deck":
        """
        Validate deck composition rules:
        - Exactly 100 cards total
        - 1-2 commanders (for partner support)
        """
        if not self.cards:
            raise ValueError("Deck must contain cards")

        # Check total card count
        total_cards = sum(card.quantity for card in self.cards)
        if total_cards != 100:
            raise ValueError(
                f"Deck must contain exactly 100 cards, got {total_cards} " f"(deck_id: {self.id})"
            )

        # Check commander count
        commander_count = sum(1 for card in self.cards if card.is_commander)
        if commander_count < 1:
            raise ValueError(f"Deck must have at least 1 commander (deck_id: {self.id})")
        if commander_count > 2:
            raise ValueError(
                f"Deck cannot have more than 2 commanders, got {commander_count} "
                f"(deck_id: {self.id})"
            )

        return self

    @property
    def commanders(self) -> list[Card]:
        """Return list of commander cards in the deck."""
        return [card for card in self.cards if card.is_commander]

    @property
    def non_commanders(self) -> list[Card]:
        """Return list of non-commander cards in the deck."""
        return [card for card in self.cards if not card.is_commander]

    class Config:
        """Pydantic configuration."""

        frozen = False


class Dataset(BaseModel):
    """
    Dataset manager for loading and managing Commander decks.

    Handles:
    - Loading decks from CSV files organized by commander
    - Train/test split (80/20)
    - Data validation and preprocessing

    Attributes:
        data_dir (Path): Root directory containing commander subdirectories
        decks (List[Deck]): List of loaded decks
        commanders (Dict[str, List[Deck]]): Decks organized by commander name
    """

    data_dir: Path = Field(..., description="Root data directory")
    decks: list[Deck] = Field(default_factory=list, description="All loaded decks")
    commanders: dict[str, list[Deck]] = Field(
        default_factory=dict, description="Decks by commander"
    )

    @field_validator("data_dir")
    @classmethod
    def validate_data_dir(cls, v: Path) -> Path:
        """Validate data directory exists."""
        if isinstance(v, str):
            v = Path(v)
        if not v.exists():
            raise ValueError(f"Data directory does not exist: {v}")
        if not v.is_dir():
            raise ValueError(f"Data path is not a directory: {v}")
        return v

    class Config:
        """Pydantic configuration."""

        arbitrary_types_allowed = True

    def load_deck_from_csv(self, csv_path: Path, commander_name: str) -> Deck:
        """
        Load a single deck from CSV file.

        Args:
            csv_path (Path): Path to CSV file
            commander_name (str): Name of the commander (from parent directory)

        Returns:
            Deck: Loaded and validated deck instance

        Raises:
            FileNotFoundError: If CSV file doesn't exist
            ValueError: If deck data is invalid
        """
        logger.info(f"Loading deck from {csv_path}")

        if not csv_path.exists():
            raise FileNotFoundError(f"Deck file not found: {csv_path}")

        # Extract deck ID from filename (without .csv extension)
        deck_id = csv_path.stem

        # Load CSV with semicolon separator
        df = pd.read_csv(csv_path, sep=";")
        logger.debug(f"Loaded {len(df)} card entries from {csv_path.name}")

        # Parse cards
        cards: list[Card] = []
        for _, row in df.iterrows():
            card = Card(
                name=str(row["Card Name"]).strip(),
                quantity=int(row["Quantity"]),
                is_commander=(str(row["Commander"]).upper() == "YES"),
            )
            cards.append(card)

        # Parse deck type
        deck_type_str = str(row.get("Deck Type", "")).strip() if "Deck Type" in df.columns else ""
        if deck_type_str == "CEDH":
            deck_type = DeckType.CEDH
        elif deck_type_str == "BUDGET":
            deck_type = DeckType.BUDGET
        else:
            deck_type = DeckType.CASUAL

        # Create deck instance (will trigger validation)
        deck = Deck(
            id=deck_id,
            commander_name=commander_name,
            cards=cards,
            deck_type=deck_type,
            date_created=str(df.iloc[0].get("Date Created"))
            if "Date Created" in df.columns
            else None,
            date_modified=str(df.iloc[0].get("Date Modified"))
            if "Date Modified" in df.columns
            else None,
        )

        logger.info(
            f"Loaded deck {deck_id} with {len(deck.commanders)} commander(s): "
            f"{', '.join(c.name for c in deck.commanders)}"
        )

        return deck

    def load_commander_decks(self, commander_name: str) -> list[Deck]:
        """
        Load all decks for a specific commander.

        Args:
            commander_name (str): Name of the commander

        Returns:
            List[Deck]: List of loaded decks

        Raises:
            FileNotFoundError: If commander directory doesn't exist
        """
        logger.info(f"Loading all decks for commander: {commander_name}")

        commander_dir = self.data_dir / commander_name
        if not commander_dir.exists():
            raise FileNotFoundError(f"Commander directory not found: {commander_dir}")

        csv_files = list(commander_dir.glob("*.csv"))
        logger.info(f"Found {len(csv_files)} deck(s) for {commander_name}")

        loaded_decks: list[Deck] = []
        for csv_file in csv_files:
            try:
                deck = self.load_deck_from_csv(csv_file, commander_name)
                loaded_decks.append(deck)
            except Exception as e:
                logger.error(f"Failed to load {csv_file.name}: {e}")
                continue

        logger.info(f"Successfully loaded {len(loaded_decks)} deck(s) for {commander_name}")
        return loaded_decks

    def load_all_decks(self) -> None:
        """
        Load all decks from all commander subdirectories.

        Updates:
            self.decks: Populated with all loaded decks
            self.commanders: Organized by commander name
        """
        logger.info(f"Loading all decks from {self.data_dir}")

        # Get all commander directories
        commander_dirs = [d for d in self.data_dir.iterdir() if d.is_dir()]
        logger.info(f"Found {len(commander_dirs)} commander(s)")

        self.decks.clear()
        self.commanders.clear()

        for commander_dir in commander_dirs:
            commander_name = commander_dir.name
            try:
                decks = self.load_commander_decks(commander_name)
                self.decks.extend(decks)
                self.commanders[commander_name] = decks
            except Exception as e:
                logger.error(f"Failed to load decks for {commander_name}: {e}")
                continue

        logger.info(
            f"Total loaded: {len(self.decks)} decks across {len(self.commanders)} commander(s)"
        )

    def to_dataframe(self) -> pd.DataFrame:
        """
        Convert all loaded decks to a pandas DataFrame for analysis.

        Returns:
            pd.DataFrame: DataFrame with columns:
                - deck_id
                - commander_name
                - card_name
                - card_quantity
                - is_commander
                - deck_type
        """
        logger.info("Converting decks to DataFrame")

        rows = []
        for deck in self.decks:
            for card in deck.cards:
                rows.append(
                    {
                        "deck_id": deck.id,
                        "commander_name": deck.commander_name,
                        "card_name": card.name,
                        "card_quantity": card.quantity,
                        "is_commander": card.is_commander,
                        "deck_type": deck.deck_type.value,
                    }
                )

        df = pd.DataFrame(rows)
        logger.info(f"Created DataFrame with {len(df)} rows")
        return df

    def train_test_split(
        self, test_size: float = 0.2, random_state: int | None = None
    ) -> tuple[list[Deck], list[Deck]]:
        """
        Split decks into training and test sets (80/20 by default).

        Args:
            test_size (float): Proportion of test set (default: 0.2)
            random_state (Optional[int]): Random seed for reproducibility

        Returns:
            tuple[List[Deck], List[Deck]]: (train_decks, test_decks)
        """
        from sklearn.model_selection import train_test_split

        logger.info(f"Splitting {len(self.decks)} decks (test_size={test_size})")

        train_decks, test_decks = train_test_split(
            self.decks, test_size=test_size, random_state=random_state
        )

        logger.info(f"Split: {len(train_decks)} train, {len(test_decks)} test")
        return train_decks, test_decks
