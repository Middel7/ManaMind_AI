# AGENTS.md - Guidelines pour l'Agent IA ManaMind

## 🎯 Objectif

Ce document définit les instructions et directives que l'agent IA doit suivre lors du développement et de la maintenance du projet **ManaMind AI**.

---

## 📋 Contexte du Projet

### Vue d'ensemble
- **Projet**: ManaMind AI - Système de recommandation pour Magic: The Gathering (format Commander/EDH)
- **Statut**: Proof of Concept (POC)
- **Objectif**: Recommander des cartes à ajouter ou retirer d'un deck Commander basé sur l'analyse de decks similaires

### Documentation de référence
- **docs/PROJECT_DOCUMENTATION.md**: Documentation de gestion du projet
- **README.md**: Documentation principale du projet
- **Projet_IA_MTG_Recommandation.pptx**: Guidelines détaillées du projet
- **pyproject.toml** & **uv.lock**: Configuration et dépendances

> Cette documentation doit systématiquement être mise à jour dès qu’une information nécessaire aux utilisateurs ou aux développeurs doit être documentée.

---

## 🛠️ Gestion des Dépendances avec UV

### Règles obligatoires

L'agent DOIT **toujours** utiliser `uv` pour la gestion des packages Python.
L'agent DOIT **toujours** se référer à Projet_IA_MTG_Recommandation.pptx comme document de contexte principal pour le développement.

#### Commandes autorisées uniquement

```bash
# Ajouter une dépendance
uv add <package-name>

# Ajouter une dépendance de développement
uv add --dev <package-name>

# Retirer une dépendance
uv remove <package-name>

# Synchroniser l'environnement
uv sync

# Installer le projet en mode éditable
uv pip install -e .

# Exécuter un script
uv run python src/manamind/script.py
```

#### ⚠️ Commandes INTERDITES

```bash
# ❌ NE JAMAIS utiliser pip directement
pip install <package>
pip uninstall <package>

# ❌ NE JAMAIS utiliser pip freeze
pip freeze > requirements.txt

# ❌ NE JAMAIS ignorer uv.lock
```

### Workflow de gestion des dépendances

1. **Avant d'ajouter une dépendance**: Vérifier si elle existe déjà dans `pyproject.toml`
2. **Après ajout**: Toujours exécuter `uv sync` pour mettre à jour l'environnement
3. **Commit**: Toujours committer `pyproject.toml` ET `uv.lock` ensemble

---

## 🐍 Principes de Développement Python

### Zen of Python

L'agent doit **toujours** respecter les principes du Zen of Python :

```python
import this

"""
- Beautiful is better than ugly.
- Explicit is better than implicit.
- Simple is better than complex.
- Complex is better than complicated.
- Flat is better than nested.
- Sparse is better than dense.
- Readability counts.
- Special cases aren't special enough to break the rules.
"""
```

### Bonnes pratiques obligatoires

#### 1. Code formatté


#### 2. Type hints
```python
from typing import List, Dict, Optional
import pandas as pd

def load_deck(file_path: str) -> pd.DataFrame:
    """Charge un deck depuis un fichier CSV."""
    pass

def recommend_cards(deck: pd.DataFrame, top_n: int = 10) -> List[str]:
    """Recommande les top_n cartes pour un deck."""
    pass
```

#### 3. Docstrings
- Format: Google Style ou NumPy Style
- Obligatoire pour toutes les fonctions publiques

```python
def analyze_commander_synergy(commander: str, cards: List[str]) -> float:
    """
    Analyse la synergie entre un commandant et un ensemble de cartes.

    Args:
        commander (str): Nom du commandant
        cards (List[str]): Liste des noms de cartes à analyser

    Returns:
        float: Score de synergie entre 0.0 et 1.0

    Raises:
        ValueError: Si le commandant n'existe pas dans la base de données
    """
    pass
```

---

## 📁 Structure du Projet

### Organisation des fichiers source

```
ManaMind_AI/
├── src/
│   └── manamind/
│       ├── __init__.py
│       ├── ingestor.py       # Ingestion des données CSV
│       ├── cleaner.py         # Nettoyage et préparation des données
│       ├── dataset.py         # Création des datasets train/test
│       ├── recommender.py     # Systèmes de recommandation
│       ├── evaluator.py       # Évaluation des modèles
│       └── utils.py           # Utilitaires communs
├── data/                      # Données des decks par commandant
├── notebooks/                 # Jupyter notebooks pour exploration
├── tests/                     # Tests unitaires
├── pyproject.toml            # Configuration du projet
└── uv.lock                   # Lock file des dépendances
```

### Règles de structuration

#### 1. Un fichier = Une responsabilité principale

```python
# ✅ BON: ingestor.py
"""Module responsable de l'ingestion des données CSV."""

def load_deck_from_csv(file_path: str) -> pd.DataFrame:
    pass

def load_commander_decks(commander_name: str) -> List[pd.DataFrame]:
    pass
```

```python
# ❌ MAUVAIS: Ne pas mélanger responsabilités
"""Module qui fait tout."""

def load_deck():
    pass

def clean_data():
    pass

def recommend():
    pass
```

#### 2. Modules principaux obligatoires

- **`ingestor.py`**: Lecture des fichiers CSV, parsing des données
- **`cleaner.py`**: Nettoyage, validation, normalisation des données
- **`dataset.py`**: Création et split des datasets (train 80% / test 20%)
- **`recommender.py`**: Implémentation des systèmes de recommandation
- **`evaluator.py`**: Métriques et évaluation des recommandations
- **`utils.py`**: Fonctions utilitaires réutilisables

---

## 📊 Gestion des Données

### Format des fichiers CSV

#### Structure attendue
```csv
Card Name;Quantity;Commander;Date Created;Date Modified;Deck Type
Eluge, the Shoreless Sea;1;YES;2024-09-06T10:43:15.747Z;2024-09-06T18:05:19.557Z;
Counterspell;1;NO;2024-09-06T10:43:15.747Z;2024-09-06T18:05:19.557Z;
```

#### Règles de parsing

```python
# Séparateur: point-virgule
df = pd.read_csv(file_path, sep=';')

# Validation obligatoire
card_count = df['Quantity'].sum()
assert 90 <= card_count <= 130, "Un deck doit contenir entre 90 et 130 cartes"
assert (df['Commander'] == 'YES').sum() >= 1, "Au moins 1 commandant requis"
assert (df['Commander'] == 'YES').sum() <= 2, "Maximum 2 commandants (partners)"
```

#### Traitement des cartes double-face

```python
# Cartes avec // dans le nom
# Exemple: "Commit // Memory"
def normalize_card_name(card_name: str) -> str:
    """Normalise le nom d'une carte (gestion des double-face)."""
    # Conserver le format original avec //
    return card_name.strip()
```

### Librairies pour exploration

#### Stack Data Science obligatoire

```python
import pandas as pd          # Manipulation de données
import numpy as np           # Calculs numériques
import logging              # Logging
from pathlib import Path    # Gestion des chemins de fichiers
```

```bash
# Installation si nécessaire
uv add pandas numpy
```

---

## 🤖 Systèmes de Recommandation

### Référence: Microsoft Recommenders

L'agent DOIT se référer au repository officiel:
**https://github.com/recommenders-team/recommenders**

#### Installation

```bash
uv add recommenders
```

#### Approches à implémenter

##### 1. Recommandation par popularité

```python
def recommend_by_popularity(
    commander: str,
    decks_library: pd.DataFrame,
    top_n: int = 10
) -> List[str]:
    """
    Recommande les cartes les plus populaires dans les decks similaires.

    Args:
        commander: Nom du commandant
        decks_library: DataFrame contenant tous les decks
        top_n: Nombre de recommandations à retourner

    Returns:
        Liste des noms de cartes recommandées
    """
    logger.info(f"Generating popularity-based recommendations for {commander}")
    # Implémentation
    pass
```

##### 2. Content-Based Filtering

```python
def recommend_content_based(
    user_deck: pd.DataFrame,
    card_features: pd.DataFrame,
    top_n: int = 10
) -> List[str]:
    """
    Recommande des cartes basées sur les caractéristiques similaires.

    Args:
        user_deck: Deck de l'utilisateur
        card_features: Features des cartes (type, couleur, CMC, etc.)
        top_n: Nombre de recommandations

    Returns:
        Liste des cartes recommandées
    """
    logger.info("Generating content-based recommendations")
    # Utiliser les métriques de similarité (cosine, euclidean, etc.)
    pass
```

### Métriques d'évaluation

Utiliser les métriques standards de recommandation:

```python
from recommenders.evaluation import (
    precision_at_k,
    recall_at_k,
    ndcg_at_k,
    map_at_k
)

def evaluate_recommendations(
    true_cards: List[str],
    recommended_cards: List[str],
    k: int = 10
) -> Dict[str, float]:
    """
    Évalue la qualité des recommandations.

    Returns:
        Dictionnaire avec precision@k, recall@k, ndcg@k, map@k
    """
    logger.info(f"Evaluating recommendations at k={k}")
    # Calcul des métriques
    pass
```

---

## 📝 Logging

### Configuration obligatoire

```python
import logging
from pathlib import Path

# Configuration du logger (dans chaque module)
logger = logging.getLogger(__name__)

# Configuration globale (dans __init__.py ou main)
def setup_logging(log_level: str = "INFO"):
    """Configure le système de logging."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/manamind.log'),
            logging.StreamHandler()
        ]
    )
```

### Règles de logging

```python
# ✅ BON: Logs informatifs à chaque étape importante
logger.info(f"Loading deck from {file_path}")
logger.info(f"Loaded {len(df)} cards")
logger.debug(f"Commander: {commander_name}")

# ⚠️ Warnings pour situations anormales mais gérables
if deck_size != 100:
    logger.warning(f"Deck size is {deck_size}, expected 100")

# ❌ Erreurs pour exceptions
try:
    df = pd.read_csv(file_path)
except FileNotFoundError:
    logger.error(f"File not found: {file_path}")
    raise
```

---

## 🧪 Tests et Qualité

### Tests unitaires obligatoires

```python
# tests/test_ingestor.py
import pytest
from manamind.ingestor import load_deck_from_csv

def test_load_valid_deck():
    """Test le chargement d'un deck valide."""
    df = load_deck_from_csv("data/Eluge_the_Shoreless_Sea/_2OUaTsNDEqPAXzZfttUlQ.csv")
    assert len(df) > 0
    assert df['Quantity'].sum() == 100

def test_load_invalid_deck():
    """Test le chargement d'un deck invalide."""
    with pytest.raises(ValueError):
        load_deck_from_csv("data/invalid.csv")
```

```bash
# Installation pytest
uv add --dev pytest

# Exécution des tests
uv run pytest tests/
```

---

## 🔄 Workflow de Développement

### Avant chaque modification de code

1. **Lire le README.md** pour comprendre le contexte
2. **Vérifier pyproject.toml** pour les versions des dépendances
3. **Consulter les guidelines** dans Projet_IA_MTG_Recommandation.pptx
4. **Vérifier la structure** du projet pour placer le code au bon endroit

### Lors de l'ajout de fonctionnalité

1. **Créer/Modifier le module approprié** (ingestor, preprocessor, dataset, recommender, evaluator)
2. **Ajouter les imports nécessaires**
3. **Implémenter avec type hints et docstrings**
4. **Ajouter des logs informatifs**
5. **Formater le code** avec `ruff check --fix`
6. **Tester manuellement** ou avec pytest

### Exemple complet

```python
# src/manamind/ingestor.py
"""Module d'ingestion des données de decks Commander."""

import logging
from pathlib import Path
from typing import List, Optional
import pandas as pd

logger = logging.getLogger(__name__)


def load_deck_from_csv(file_path: str) -> pd.DataFrame:
    """
    Charge un deck depuis un fichier CSV.

    Args:
        file_path (str): Chemin vers le fichier CSV du deck

    Returns:
        pd.DataFrame: DataFrame contenant les cartes du deck

    Raises:
        FileNotFoundError: Si le fichier n'existe pas
        ValueError: Si le deck n'est pas valide (≠ 100 cartes)
    """
    logger.info(f"Loading deck from {file_path}")

    path = Path(file_path)
    if not path.exists():
        logger.error(f"File not found: {file_path}")
        raise FileNotFoundError(f"Deck file not found: {file_path}")

    df = pd.read_csv(file_path, sep=';')
    logger.debug(f"Loaded {len(df)} card entries")

    # Validation
    total_cards = df['Quantity'].sum()
    if total_cards != 100:
        logger.warning(f"Deck contains {total_cards} cards, expected 100")

    commanders = df[df['Commander'] == 'YES']
    logger.info(f"Found {len(commanders)} commander(s): {', '.join(commanders['Card Name'].tolist())}")

    return df


def load_commander_decks(commander_name: str, data_dir: str = "data") -> List[pd.DataFrame]:
    """
    Charge tous les decks d'un commandant spécifique.

    Args:
        commander_name (str): Nom du commandant
        data_dir (str): Répertoire racine des données

    Returns:
        List[pd.DataFrame]: Liste des DataFrames de decks

    Raises:
        FileNotFoundError: Si le dossier du commandant n'existe pas
    """
    logger.info(f"Loading all decks for commander: {commander_name}")

    commander_dir = Path(data_dir) / commander_name
    if not commander_dir.exists():
        logger.error(f"Commander directory not found: {commander_dir}")
        raise FileNotFoundError(f"No data found for commander: {commander_name}")

    csv_files = list(commander_dir.glob("*.csv"))
    logger.info(f"Found {len(csv_files)} deck(s) for {commander_name}")

    decks = []
    for csv_file in csv_files:
        try:
            deck = load_deck_from_csv(str(csv_file))
            decks.append(deck)
        except Exception as e:
            logger.error(f"Failed to load {csv_file.name}: {e}")
            continue

    logger.info(f"Successfully loaded {len(decks)} deck(s)")
    return decks
```

---

## 📚 Ressources et Références

### Documentation officielle
- **Python**: https://docs.python.org/3.12/
- **UV**: https://github.com/astral-sh/uv
- **Pandas**: https://pandas.pydata.org/docs/
- **NumPy**: https://numpy.org/doc/
- **Microsoft Recommenders**: https://github.com/recommenders-team/recommenders

### Magic: The Gathering
- **Scryfall API**: https://scryfall.com/docs/api
- **EDHREC**: https://edhrec.com/ (référence pour decks Commander)

### Best Practices
- **PEP 8**: https://pep8.org/
- **Type Hints**: https://docs.python.org/3/library/typing.html
- **Logging**: https://docs.python.org/3/howto/logging.html

---

## ✅ Checklist avant chaque commit

- [ ] Code formatté avec ruff check et ruff format
- [ ] Type hints présents sur toutes les fonctions publiques
- [ ] Docstrings complètes (Args, Returns, Raises)
- [ ] Logs ajoutés aux étapes importantes
- [ ] `pyproject.toml` et `uv.lock` synchronisés
- [ ] Code respecte le Zen of Python

---

## 🚨 Points d'attention critiques

### ⚠️ À TOUJOURS faire
- ✅ Utiliser `uv` pour toute gestion de dépendance
- ✅ Consulter README.md avant toute modification
- ✅ Vérifier les versions dans pyproject.toml
- ✅ Ajouter des logs informatifs
- ✅ Respecter la structure modulaire (un fichier = une responsabilité)

### ❌ À NE JAMAIS faire
- ❌ Utiliser `pip` directement
- ❌ Utiliser `uv pip install` au lieu de `uv add` ou `uv remove`
- ❌ Utiliser `uv run python main.py` au lieu de `PYTHONPATH=. uv run main.py`
- ❌ Ignorer les erreurs silencieusement
- ❌ Mélanger plusieurs responsabilités dans un même module
- ❌ Oublier les type hints et docstrings
- ❌ Committer du code non formatté
- ❌ Modifier `uv.lock` manuellement

---

## 📌 Version

- **Document Version**: 1.0.0
- **Date**: 2024-11-16
- **Auteur**: ManaMind AI Project
- **Status**: Living Document (à mettre à jour régulièrement)

---

*Ce document est la référence absolue pour le développement du projet ManaMind AI. Toute déviation doit être justifiée et documentée.*