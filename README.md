# ManaMind AI 🧙‍♂️

> Système de recommandation intelligent pour Magic: The Gathering utilisant l'apprentissage automatique

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Status](https://img.shields.io/badge/status-POC-orange.svg)]()

# Description du projet

ManaMind AI est un Proof of Concept pour désigner un moteur de recommandation basé sur l'IA qui analyse les decks existants d'un joueur de ```Magic: The Gathering``` dans le style de jeu `Commander` ou `EDH`. En fonction des cartes déjà présentes dans les decks, le système aura pour but de réaliser les actions suivantes :
- Suggestion des cartes manquantes dans un deck en fonction de son commandant et en prenant pour référence une bibliothèque de decks.
- Suggestion des cartes à retirer d’un deck en fonction de son commandant et en prenant pour référence une bibliothèque de decks.

Systèmes de recommandations testées :
- Par popularité des cartes dans les decks similaires
- Content-based filtering (analyse des caractéristiques des cartes)

## 🛠️ Technologies

- **Python 3.12**
- **ML Framework**: scikit-learn / TensorFlow / PyTorch (à définir)
- **Data Processing**: pandas, numpy
- **MTG Data**: API Scryfall

# Contexte

## Magic: The Gathering (MTG)
- Jeu de cartes à collectionner créé par Richard Garfield et publié par Wizards of the Coast en 1993.
- Style de jeu communéments appelés `Commander` ou `EDH`
- Un deck contient généralement 100 cartes dont un commandant légendaire.
- Les principales catégories de cartes : créatures, artefacts, enchantements, terrains, éphémères, rituels, planeswalkers
- Toutes les cartes sont uniques (sauf terrains de base)

## Données
Le dossier `/data` contient des sous-dossiers nommés par le nom du Commandant. Chaque sous-dossier contient des fichiers CSV chacun un deck de 100 cartes construit autour de ce commandant.
Le schéma des fichiers CSV dont le séparateur est `;` est le suivant :
- `Card Name` (string): Nom de la carte en anglais qui suit une casse standardisée. Exemple : "Black Lotus", "Counterspell", "Nicol Bolas, the Ravager, The Flood of Mars, You Find the Villains'Lair". Si une carte a un nom composé de deux cartes, les deux noms sont séparés par ` // `. Exemple : `Commit // Memory`
- `Quantity` (int, ge>=1): nombre de copies de la carte dans le deck, conversion en entier. La somme des entiers doit être égale à 100.
- `Commander` (string) : indique si la carte est le commandant du deck. Liste des valeurs autorisées : "YES", "NO". Il y a toujours au moins 1 carte avec la valeur "YES" par deck et 99 cartes avec la valeur "NO". Il ne peut y avoir plus de 2 cartes avec la valeur "YES" (spécificité pour les commandants partenaires).
- `Date Created` (timestamp) : date de création du deck.
- `Date Modified` (timestamp) : dernière date de modification du deck.
- `Deck Type` (string) : Selon les valeurs suivantes :
    - `CEDH` : Deck compétitif EDH
    - `BUDGET` : Deck EDH par type de budget (ex: Budget 50$, Budget 75$, maximum 100$)
    - Non spécifié (vide) : Deck casual EDH

## Règles de nettoyage et de préparation des données

## Règles de séparation des données

Méthodologie de séparation des données d'entraînement et de test :
- Pour chaque commandant, les decks sont divisés en ensembles d'entraînement (80%)


## Système de recommandation
- Par popularité des cartes dans les decks similaires
- Content-based filtering (analyse des caractéristiques des cartes)

## 🚀 Quick Start

### Prérequis

```bash
python >= 3.12
```

### Installation

```bash
# Cloner le projet
git clone https://github.com/votre-username/ManaMind_AI.git
cd ManaMind_AI

# Setup avec uv (recommandé)
uv sync


## 🏗️ Architecture

```
┌─────────────────┐
│  User Profile   │
│   (Decks)       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Feature        │
│  Extraction     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  ML Model       │
│  (Recommender)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Ranked Cards   │
│  Suggestions    │
└─────────────────┘
```


## 🤝 Contribution

Ce projet est en phase POC. Les contributions sont les bienvenues !

## 📄 License

MIT License - voir [LICENSE](LICENSE)