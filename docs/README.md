# Documentation ManaMind AI

Ce dossier contient la documentation complète du projet **ManaMind AI**.

2. Lance le bon serveur :


cd "c:\Users\fabie\Documents\GitHub\ManaMind_AI"
python server.py
3. Ouvre http://localhost:8000/

---

## 📁 Structure des Documents

### Analyses et Résultats

#### `kpi_analysis_summary.md`
**Résumé complet de l'analyse statistique du dataset**

- **Contenu**:
  - Statistiques générales (4544 decks, 7778 cartes uniques)
  - Distribution par type de deck (CEDH, BUDGET, Non spécifié)
  - Top cartes par commandant (Galadriel vs Eluge)
  - Analyse de densité (Staple, Common, Uncommon, Rare, Unique)
  - Chevauchement entre commandants (1600 cartes universelles)
  - Recommandations pour le système de recommandation
  - Insights métier et prochaines étapes

- **Utilisation**:
  ```bash
  # Consulter le document
  cat docs/kpi_analysis_summary.md
  ```

- **Visualisations associées**:
  - `outputs/top_25_cards.png` - Graphique des 25 cartes les plus populaires
  - `outputs/cumulative_frequency.png` - Courbe cumulative de Pareto

---

## 🎯 Documents de Référence

### Documents Racine

#### `../README.md`
**Documentation principale du projet**

- Vue d'ensemble du projet ManaMind AI
- Installation et configuration
- Structure du code
- Exemples d'utilisation
- Roadmap et objectifs

#### `../AGENTS.md`
**Guidelines pour l'Agent IA**

- Principes de développement (Zen of Python)
- Gestion des dépendances avec UV
- Structure des modules (ingestor, cleaner, dataset, recommender, evaluator)
- Bonnes pratiques (type hints, docstrings, logging)
- Workflow de développement
- Checklist avant commit

---

## 📊 Comment Utiliser cette Documentation

### 1. Pour Comprendre le Dataset
👉 Lire **`kpi_analysis_summary.md`** en premier

### 2. Pour Développer
👉 Consulter **`../AGENTS.md`** pour les guidelines

### 3. Pour Démarrer le Projet
👉 Suivre **`../README.md`** pour l'installation

---

## 🔄 Mise à Jour de la Documentation

### Scripts retirés
- Les scripts `src/manamind/build_card_database_detailed.py`, `src/manamind/build_card_database_final.py`, `src/manamind/build_card_database_fixed.py` et `src/manamind/build_card_database.py` ont été supprimés.

### Quand mettre à jour ?

- **`kpi_analysis_summary.md`**: 
  - Après chaque nouvelle analyse du dataset
  - Lors de l'ajout de nouvelles métriques
  - Si les statistiques changent significativement

### Comment générer les analyses ?

```bash
# 1. Générer les KPI et visualisations
PYTHONPATH=src uv run examples/test_kpi.py

# 2. Vérifier les résultats
ls -lh outputs/

# 3. Mettre à jour le document si nécessaire
vim docs/kpi_analysis_summary.md
```

---

## 📝 Style de Documentation

### Principes

1. **Markdown** pour tous les documents
2. **Tableaux** pour les données structurées
3. **Emojis** pour la navigation visuelle
4. **Code blocks** avec syntaxe highlighting
5. **Liens internes** pour la navigation

### Format des Statistiques

```markdown
| Métrique | Valeur | Description |
|----------|--------|-------------|
| Total decks | 4544 | Decks valides |
| Cartes uniques | 7778 | Pool total |
```

### Format des Insights

```markdown
## 💡 Insight

**Observation**: Description de l'observation

**Impact**: Conséquences pour le projet

**Action**: Recommandation
```

---

## 🚀 Prochains Documents à Créer

### Phase 1: Modélisation
- [ ] `model_architecture.md` - Architecture du système de recommandation
- [ ] `features_engineering.md` - Extraction et transformation des features
- [ ] `evaluation_metrics.md` - Détails sur Precision@k, Recall@k, NDCG

### Phase 2: Résultats
- [ ] `baseline_results.md` - Résultats du modèle baseline (popularité)
- [ ] `content_based_results.md` - Résultats du content-based filtering
- [ ] `collaborative_results.md` - Résultats du collaborative filtering

### Phase 3: Déploiement
- [ ] `api_documentation.md` - Documentation de l'API FastAPI
- [ ] `deployment_guide.md` - Guide de déploiement en production
- [ ] `monitoring.md` - Métriques de monitoring et alertes

---

## 📚 Ressources Externes

### Magic: The Gathering
- [Scryfall API](https://scryfall.com/docs/api) - Données des cartes
- [EDHREC](https://edhrec.com/) - Statistiques Commander
- [MTG Wiki](https://mtg.fandom.com/) - Règles et lore

### Machine Learning
- [Microsoft Recommenders](https://github.com/recommenders-team/recommenders) - Librairie de référence
- [Surprise](http://surpriselib.com/) - Collaborative filtering
- [LightFM](https://making.lyst.com/lightfm/docs/home.html) - Hybrid recommenders

### Outils Python
- [UV](https://github.com/astral-sh/uv) - Gestionnaire de packages
- [Pydantic](https://docs.pydantic.dev/) - Validation de données
- [Pandas](https://pandas.pydata.org/) - Analyse de données

---

## ✅ Checklist de Qualité

Avant de committer un nouveau document:

- [ ] Markdown valide (pas d'erreurs de syntaxe)
- [ ] Tableaux alignés correctement
- [ ] Code blocks avec syntaxe highlighting
- [ ] Liens internes fonctionnels
- [ ] Emojis cohérents
- [ ] Date de version présente
- [ ] Références aux fichiers sources
- [ ] Exemples de code testés

---

**Documentation maintenue par l'équipe ManaMind AI**  
**Dernière mise à jour**: 2024-11-16
