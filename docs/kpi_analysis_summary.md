# ManaMind AI - Résultats d'Analyse KPI

**Date**: 2024-11-16  
**Version**: 1.0.0  
**Dataset**: 4544 decks Commander (Galadriel + Eluge)

---

## 📊 Statistiques Générales

### Volume de Données
- **Total de decks**: 4544 decks valides
  - Galadriel, Light of Valinor: 2458 decks (54.1%)
  - Eluge, the Shoreless Sea: 2086 decks (45.9%)
- **Total de cartes uniques**: 7778 cartes
- **Total d'instances de cartes**: ~454,400 cartes (4544 × 100)

### Qualité des Données
- **Decks rejetés**: ~575 decks (~11% du total)
  - Raison principale: nombre de cartes ≠ 100
  - Exemples: 101, 102, 104, 106, 107, 110, 114, 119, 125, 157 cartes

---

## 🎯 Distribution par Type de Deck

| Type de Deck | Nombre | Pourcentage |
|--------------|--------|-------------|
| **Non spécifié** | 4390 | 96.61% |
| **BUDGET** | 83 | 1.83% |
| **CEDH** | 71 | 1.56% |

**Observation**: La majorité écrasante des decks (96.6%) n'ont pas de type spécifié, ce qui suggère:
- Les utilisateurs ne renseignent pas systématiquement cette information
- Opportunité d'améliorer la collecte de métadonnées
- Nécessité d'inférer le type de deck via analyse des cartes

---

## ⭐ Top 10 Cartes les Plus Populaires (Global)

| Rang | Carte | Decks | Popularité |
|------|-------|-------|------------|
| 1 | **Island** | 4305 | **94.7%** |
| 2 | **Sol Ring** | 3809 | **83.8%** |
| 3 | **Arcane Signet** | 3189 | **70.2%** |
| 4 | **Counterspell** | 2400 | **52.8%** |
| 5 | **Reliquary Tower** | 2381 | **52.4%** |
| 6 | **Command Tower** | 2362 | **52.0%** |
| 7 | **Forest** | 2353 | **51.8%** |
| 8 | **Plains** | 2284 | **50.3%** |
| 9 | **Cyclonic Rift** | 1735 | **38.2%** |
| 10 | **Mystic Sanctuary** | 1666 | **36.7%** |

### Insights Clés
- **Sol Ring** reste l'auto-include universel (83.8% des decks)
- **Arcane Signet** est le 2e artefact le plus populaire (70.2%)
- **Counterspell** est le contre-sort le plus joué (52.8%)
- **Cyclonic Rift** confirme sa réputation de carte incontournable en Commander

---

## 🔍 Analyse par Commandant

### Galadriel, Light of Valinor (2458 decks)

**Top 5 Cartes**:
| Rang | Carte | Decks | Popularité |
|------|-------|-------|------------|
| 1 | Forest | 2353 | 95.7% |
| 2 | Island | 2313 | 94.1% |
| 3 | Plains | 2284 | 92.9% |
| 4 | Command Tower | 2293 | 93.3% |
| 5 | Sol Ring | 2109 | 85.8% |

**Insights**:
- Deck **Bant** (Bleu/Vert/Blanc) confirmé
- Forte présence de terrains de base (95%+)
- Command Tower quasi-obligatoire (93.3%)

---

### Eluge, the Shoreless Sea (2086 decks)

**Top 5 Cartes**:
| Rang | Carte | Popularité |
|------|-------|------------|
| 1 | Island | 95.5% |
| 2 | Snow-Covered Island | 5.6% |
| 3 | Sol Ring | 81.5% |
| 4 | Mystic Sanctuary | 78.2% |
| 5 | Sapphire Medallion | 76.2% |

**Insights**:
- Deck **Mono-Bleu** confirmé
- Thématique "Neige" (Snow-Covered Island: 5.6%)
- Mystic Sanctuary ultra-populaire (78.2% vs 36.7% global)
- Sapphire Medallion fortement présent (76.2%)

---

## 📈 Analyse de Densité des Cartes

### Distribution de la Popularité

| Catégorie | Nombre | % du Total | Description |
|-----------|--------|-----------|-------------|
| **Staple** | 1042 | **13.4%** | 50+ decks (cartes incontournables) |
| **Common** | 622 | **8.0%** | 21-50 decks |
| **Uncommon** | 1540 | **19.8%** | 6-20 decks |
| **Rare** | 2435 | **31.3%** | 2-5 decks |
| **Unique** | 2139 | **27.5%** | 1 seul deck |

### Observations

#### 📊 Principe de Pareto Confirmé
- **Top 128 cartes** (1.6% du total) = **50% des apparitions**
- **Top 1042 cartes** (13.4% du total) = Staples universels

#### 🎨 Diversité des Decks
- **27.5% de cartes uniques** = forte personnalisation
- **31.3% de cartes rares** = expérimentation active
- Chaque deck a sa "signature" propre

---

## 🔗 Chevauchement entre Commandants

### Cartes Universelles
- **1600 cartes** apparaissent dans les decks des **2 commandants**
- Représentent ~20.6% du pool total (1600/7778)

### Exemples de Cartes Universelles

#### 🏆 Auto-Includes
- Sol Ring
- Arcane Signet
- Counterspell
- Reliquary Tower
- Cyclonic Rift

#### 🌊 Thématique Bleue
- Island (évident)
- Mystic Sanctuary
- Rhystic Study
- Consecrated Sphinx

#### 🛡️ Artefacts Utilitaires
- Lightning Greaves
- Swiftfoot Boots
- Skullclamp

---

## 💡 Recommandations pour le Système de Recommandation

### 1. Stratégie Content-Based

**Features à extraire**:
```python
features = {
    'cmc': float,                    # Coût de mana converti
    'colors': List[str],             # Couleurs (W, U, B, R, G)
    'type': str,                     # Type (Creature, Instant, etc.)
    'commander_synergy': float,      # Score de synergie calculé
    'popularity_global': float,      # % de présence global
    'popularity_commander': float,   # % de présence pour ce commander
}
```

### 2. Filtrage Collaboratif

**Similarité Jaccard recommandée**:
- Basée sur les 1600 cartes universelles
- Pondération par popularité
- Clustering des decks similaires

### 3. Modèle Hybride (Optimal)

**Pipeline suggéré**:
1. **Filtrage initial**: Éliminer les cartes incompatibles (couleurs)
2. **Scoring collaboratif**: Identifier les decks similaires
3. **Features content-based**: Affiner les recommandations
4. **Post-processing**: Équilibrer courbe de mana, types de cartes

---

## 🎯 Métriques d'Évaluation

### Baseline (Popularité Pure)
- **Precision@10**: Mesurer si les 10 premières recommandations sont dans le deck
- **Recall@25**: Mesurer combien de cartes du deck sont dans les top 25
- **NDCG@10**: Évaluer l'ordre des recommandations

### Objectifs Cibles
```python
targets = {
    'precision@10': 0.40,   # 4 cartes sur 10 dans le deck
    'recall@25': 0.30,      # 30% des cartes du deck couvertes
    'ndcg@10': 0.45,        # Bon ordre de recommandation
}
```

---

## 🔬 Insights Métier

### 1. Format Commander Confirmé
- **100 cartes exactement** (validation stricte)
- **1-2 commandants** (partners possibles)
- Forte cohérence des données

### 2. Méta-Game Identifiable
- **Sol Ring** = auto-include universel
- **Counterspell** = contrôle omniprésent
- **Cyclonic Rift** = board wipe préféré

### 3. Personnalisation Élevée
- 27.5% de cartes uniques
- Diversité des stratégies
- Innovation constante

### 4. Thématiques Distinctes
- **Galadriel**: Bant (contrôle/value)
- **Eluge**: Mono-U (tempo/bounce)

---

## 📁 Fichiers Générés

### Visualisations
- `outputs/top_25_cards.png` - Graphique des 25 cartes les plus populaires
- `outputs/cumulative_frequency.png` - Courbe cumulative (Pareto)

### Données Brutes
- `src/manamind/kpi.py` - Module d'analyse complet (678 lignes)
- `examples/test_kpi.py` - Script de test et génération de rapports

---

## 🚀 Prochaines Étapes

### Immédiat (POC)
1. ✅ Dataset chargé et validé
2. ✅ KPI calculés et visualisés
3. 🔄 **Prochaine**: Implémenter système de recommandation baseline (popularité)

### Court Terme
1. **Content-Based Filtering**: Utiliser les features des cartes
2. **Collaborative Filtering**: Similarité entre decks (Jaccard)
3. **Évaluation**: Calculer métriques (Precision, Recall, NDCG)

### Moyen Terme
1. **Modèle hybride**: Combiner content-based + collaborative
2. **Fine-tuning**: Optimiser hyperparamètres
3. **API**: Exposer le système via FastAPI

---

## 📚 Références

### Documentation
- **README.md**: Vue d'ensemble du projet
- **AGENTS.md**: Guidelines de développement
- **Projet_IA_MTG_Recommandation.pptx**: Spécifications détaillées

### Code Source
- `src/manamind/dataset.py`: Modèles de données (Card, Deck, Dataset)
- `src/manamind/kpi.py`: Analyses statistiques et KPI
- `examples/test_dataset.py`: Tests de chargement
- `examples/test_kpi.py`: Tests d'analyse

---

**Document généré par ManaMind AI**  
**Version**: 1.0.0  
**Date**: 2024-11-16
