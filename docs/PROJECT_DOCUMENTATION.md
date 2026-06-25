# ManaMind AI — Documentation

> Source unique de vérité pour le projet. Mise à jour obligatoire à chaque changement d'architecture.  
> Utilisable directement comme contexte pour tout assistant externe (ChatGPT, Claude, etc.).  
> Dernière mise à jour : 2026-06-25

```powershell
uv run python server.py
# → http://localhost:8080
```

---

## 1. Objectif du projet

Système de recommandation de cartes Magic: The Gathering pour le format Commander/EDH.

- Analyser un deck fourni par l'utilisateur (fichier texte).
- Recommander des cartes à **ajouter** (popularité, synergie).
- Recommander des cartes à **retirer** (cartes hors-thème, peu jouées).
- Afficher les résultats dans une interface web avec images et prix Scryfall.

Repo : https://github.com/Middel7/ManaMind_AI — branche active : `main`

---

## 2. Stack technique

| Composant | Technologie |
|---|---|
| Langage | Python 3.12.8 (version exacte requise) |
| API Web | FastAPI + Uvicorn (`server.py`) |
| Gestion dépendances | `uv` (jamais `pip` directement) |
| Base de données | PostgreSQL (optionnelle, via `.env`) |
| ORM | SQLAlchemy 2.0 (`Mapped`/`mapped_column`) |
| Migrations | Alembic 1.13+ (gérées dans MTG-DB) |
| Package partagé | `mtgdb` — `git+https://github.com/Middel7/MTG-DB.git` |
| Linting/Format | `ruff` (line-length=100) |

---

## 3. Structure du dépôt

```
ManaMind_AI/
├── server.py                        ← Point d'entrée FastAPI
├── start.py                         ← Lanceur alternatif
├── pyproject.toml                   ← Config projet + dépendances
├── uv.lock                          ← Lock file (ne pas modifier manuellement)
├── alembic.ini / alembic/           ← Config Alembic (pointe vers mtgdb)
├── .env                             ← DATABASE_URL (ignoré par git)
├── .env.example                     ← Modèle .env
│
├── recommendations_view_slide16.html ← Page d'accueil (/)
├── commander_suggest.html           ← /commander-suggest
├── deck_build.html                  ← /deck-build (ajouts depuis collection)
├── deck_moves.html                  ← /deck-moves
├── deck_config.html                 ← /deck-config (gestion decklists Moxfield)
├── deck_trim.html                   ← /deck-trim
├── results.html                     ← /results
│
├── src/manamind/
│   ├── recommandation_populaire.py  ← Algo V1 (Analyse Populaire)
│   ├── card_commander_matcher.py    ← Index d'inclusion EDHREC, suggest_commanders()
│   ├── collection_advisor.py        ← suggest_from_collection(), suggest_moves()
│   ├── moxfield_client.py           ← Client Moxfield + gestion locale .txt
│   └── db/                          ← Re-exports depuis mtgdb (ne pas modifier)
│       ├── base.py / engine.py
│       └── models/  (card, card_face, card_price, card_printing, import_run, mtg_set)
│
├── scripts/
│   ├── compute_deck_stats.py        ← Calcul stats brutes → PostgreSQL
│   ├── compute_commander_tfidf.py   ← Profils TF-IDF par commandant (lit la DB)
│   ├── build_card2vec.py            ← Pipeline Card2Vec (Word2Vec sur decklists)
│   ├── build_ml_dataset.py          ← Dataset ML : train.csv + test.csv pour XGBoost
│   ├── evaluate_models.py           ← Évaluation XGBoost baseline vs Card2Vec
│   ├── import_scryfall_cards.py     ← Import Scryfall → PostgreSQL
│   ├── import_game_changers.py      ← Import des cartes "game changer"
│   └── validate_mtg_cards_db.py     ← Vérification intégrité DB
│
├── data/
│   ├── Decklists/                   ← 36 443 decklists dans 34 sous-dossiers
│   ├── My decks/                    ← Decklists personnelles (.txt, format Moxfield)
│   ├── moxfield_cache/              ← Cache JSON des decks Moxfield fetchés
│   ├── moxfield_decks.json          ← Config des decks Moxfield (deck_id, commander, url)
│   ├── raw/                         ← Données brutes source
│   ├── commander_aliases.json       ← Mapping alias → nom canonique (44→34)
│   └── cards_unique_global.csv      ← Liste des cartes uniques du dataset
│
├── data/embeddings/                 ← Embeddings Card2Vec (générés par build_card2vec.py)
│   ├── card2vec.model               ← Modèle Gensim rechargeable
│   ├── card_embeddings.npy          ← Matrice float32 (16 040 cartes × 128 dim)
│   ├── card_embeddings.csv          ← card_name + 128 dimensions
│   ├── card_index.json              ← { card_name: index_dans_npy }
│   ├── token_to_name.json           ← { token_normalisé: nom_original }
│   ├── card_neighbors.csv           ← 20 voisins par carte
│   ├── commander_embeddings.npy     ← Vecteurs commandants pondérés TF-IDF (34 × 128)
│   └── commander_embeddings.json    ← { commander: [float × 128] }
│
├── data/ml/                         ← Dataset ML (généré par build_ml_dataset.py)
│   ├── train.csv                    ← 82 614 lignes, 27 commandants
│   ├── test.csv                     ← 23 930 lignes, 7 commandants (hold-out)
│   └── feature_info.json            ← Stats features + liste commandants train/test
│
├── data/models/                     ← Modèles XGBoost sérialisés
│   ├── xgb_baseline.json            ← Sans Card2Vec (5 features, 219 arbres)
│   └── xgb_card2vec.json            ← Avec cosine_similarity (6 features, 312 arbres)
│
├── data/evaluation/                 ← Résultats d'évaluation
│   ├── model_comparison.csv         ← RMSE/MAE/R² baseline vs card2vec
│   ├── feature_importance.csv       ← Gain/Weight/Cover par feature
│   ├── shap_summary.csv             ← SHAP mean |val| (deux modèles)
│   ├── shap_top_features.csv        ← SHAP modèle complet
│   ├── commander_ranking_metrics.csv ← P@K, NDCG@K par commandant
│   ├── card2vec_value_report.md     ← Rapport conclusions Card2Vec
│   └── plots/                       ← PNG : feature importance, SHAP, ranking, erreurs
│
├── outputs/                         ← Résultats des recommandations (généré à la volée)
├── uploads/                         ← Decklists uploadées par l'utilisateur
└── docs/
    └── PROJECT_DOCUMENTATION.md    ← Ce fichier (documentation unique)
```

---

## 4. Gestion des dépendances

```powershell
uv add <package>          # Ajouter une dépendance
uv remove <package>       # Retirer une dépendance
uv sync                   # Synchroniser l'environnement

# Mettre à jour mtgdb après un push dans MTG-DB :
uv lock --upgrade-package mtgdb
uv sync
```

**Règle absolue** : ne jamais utiliser `pip install` / `pip uninstall` dans ce projet.  
Toujours committer `pyproject.toml` ET `uv.lock` ensemble.

---

## 5. Algorithmes de recommandation

### En production — Analyse Populaire (V1)

**Fichier** : `src/manamind/recommandation_populaire.py`  
**Route** : `POST /upload-deck`

Principe : compare le deck de l'utilisateur aux decklists de référence par co-occurrence et fréquence par commandant.

Fonctions clés :
- `normalize_name(name)` → strip, supprime le préfixe "A-" (cartes Alchemy)
- `load_deck_dataset(root)` → charge tous les CSV du dossier commandant
- `build_statistics(decks)` → Counter fréquences + Counter co-occurrence
- `recommend_additions(...)` → score = fréq commandant × co-occurrence × (1 − popularité globale)
- `recommend_removals(...)` → cartes du deck peu jouées par ce commandant

### En développement — Filtrage Collaboratif

Les données nécessaires sont déjà en base (`deck_stat_commander`, `deck_stat_global`).

Principe prévu :
1. Charger `inclusion_rate` depuis `deck_stat_commander` pour le commandant
2. Filtrer les cartes absentes du deck avec un taux > seuil
3. Scorer par combinaison `inclusion_rate` (commandant) et `idf` (discriminance globale)
4. Pas de scan CSV → réponse rapide

Fichier à créer : `src/manamind/recommandation_collaborative.py`

---

## 6. Serveur FastAPI (`server.py`)

```powershell
uv run python server.py
# → http://localhost:8080
```

### Routes — Pages

| Méthode | Route | Fichier servi |
|---|---|---|
| GET | `/` | `recommendations_view_slide16.html` |
| GET | `/results` | `results.html` |
| GET | `/commander-suggest` | `commander_suggest.html` |
| GET | `/deck-build` | `deck_build.html` |
| GET | `/deck-moves` | `deck_moves.html` |
| GET | `/deck-config` | `deck_config.html` |
| GET | `/deck-trim` | `deck_trim.html` |

> **Supprimées (2026-06-25)** : `/cards` (`cards.html`) et `/deck-suggest` (`deck_suggest.html`)

### Routes — API

| Méthode | Route | Description |
|---|---|---|
| POST | `/upload-deck` | Analyse Populaire V1 (FormData, fichier .txt) |
| POST | `/api/deck/analyze` | Deck Mentor IA (JSON `{ commander, decklist }`) |
| GET | `/api/cards/search?q=` | Recherche cartes PostgreSQL (max 100) |
| GET | `/api/cards/image?name=` | URL image carte (DB → fallback Scryfall) |
| GET | `/api/cards/price?name=` | Prix EUR minimum (DB → fallback Scryfall) |
| GET | `/api/cards/autocomplete?q=` | Autocomplete starts-with (max 8) |
| GET | `/api/moxfield-decks` | Liste des decks configurés (avec `locally_modified`) |
| POST | `/api/moxfield-decks` | Ajouter un deck Moxfield (body `{ url }`) |
| POST | `/api/moxfield-decks/{id}/refresh` | Refetch depuis Moxfield |
| DELETE | `/api/moxfield-decks/{id}` | Supprimer un deck de la config |
| GET | `/api/deck-txt/{id}` | Contenu .txt local du deck |
| POST | `/api/deck-txt/{id}/mark-synced` | Marquer le deck comme synchronisé (met à jour mtime du cache) |
| GET | `/api/deck-card/add` | Ajouter une carte au deck .txt local |
| GET | `/api/deck-card/remove` | Retirer une carte du deck .txt local |
| GET | `/api/card-inclusion?card=&commander=` | Taux d'inclusion EDHREC d'une carte pour un commandant |
| GET | `/api/collection-suggest` | Cartes de collection à inclure dans les decks |
| POST | `/api/deck-suggest-moves` | Déplacements de cartes entre decks |

> **Supprimée (2026-06-25)** : `POST /api/deck-suggest` (alimentait `/deck-suggest`)

### Logique `/upload-deck`

1. Sauvegarder le fichier dans `uploads/`
2. Parser le texte pour extraire les cartes et le commandant
3. Calculer additions (`recommend_additions`) et retraits (`recommend_removals`) via `recommandation_populaire.py`
4. Écrire le CSV dans `outputs/`
5. Retourner `{ deckFile, recommendationsFile }`

Note : depuis 2026-06-25, la page d'accueil envoie aussi les textes collés et les decks Moxfield via ce même endpoint (le texte est converti en `Blob` côté JS avant envoi FormData).

### Fonctions utilitaires (`server.py`)

- `_normalize_filename(name)` → slug snake_case ASCII
- `_extract_commander_from_deck(text)` → extrait commandant (dernière section, 1 ligne)
- `_compute_removals(deck, commander)` → retraits via algo V1
- `_json_response(data, status_code)` → JSONResponse UTF-8 sans échappement ASCII

---

---

## 7. Intégration Moxfield

### Principe

Les decks Moxfield sont configurés une fois dans `/deck-config` (URL Moxfield) puis utilisés partout dans l'application. La logique est centralisée dans `src/manamind/moxfield_client.py`.

### Fichiers de données

| Fichier | Contenu |
|---|---|
| `data/moxfield_decks.json` | Liste des decks configurés : `[{ deck_id, commander, name, url }]` |
| `data/moxfield_cache/{deck_id}.json` | Cache JSON de la réponse API Moxfield (timestamp = date du dernier fetch) |
| `data/My decks/{commander_slug}.txt` | Copie locale editable en format `1 Card Name` (timestamp = date de la dernière modif locale) |

### Fonctions clés (`moxfield_client.py`)

| Fonction | Description |
|---|---|
| `load_config()` | Lit `moxfield_decks.json` |
| `save_config(decks)` | Écrit `moxfield_decks.json` |
| `fetch_and_cache(deck_id)` | Appel API Moxfield v3 → écrit le cache JSON et le .txt local |
| `get_local_txt_content(commander)` | Lit le .txt local, retourne son contenu |
| `add_card_to_deck(commander, card_name)` | Ajoute une ligne `1 Card Name` dans le .txt |
| `remove_card_from_deck(commander, card_name)` | Retire la ligne correspondante du .txt |
| `is_locally_modified(deck_id, commander)` | `True` si mtime(.txt) > mtime(cache JSON) |
| `mark_as_synced(deck_id, commander)` | Met à jour mtime du cache JSON → efface l'état "modifié localement" |

### Détection de modifications locales

`is_locally_modified()` compare les timestamps fichier :
- `.txt` plus récent que le cache JSON → l'utilisateur a modifié le deck localement sans synchroniser Moxfield
- Dans `/deck-config` : affichage de la pill orange "Modifié localement", bouton "⚠ Copier" pulsant, "Mettre à jour" désactivé
- Après avoir copié la decklist et cliqué "Copier" : appel `POST /api/deck-txt/{id}/mark-synced` → l'état revient à normal sans rechargement de page

### API Moxfield utilisée

```
GET https://api2.moxfield.com/v3/decks/all/{deck_id}
```

Champs extraits : `boards.mainboard.cards` (quantité + nom), `commanders` pour identifier le commandant.

---

## 7b. Pages de l'interface

### Page d'accueil — `/` (`recommendations_view_slide16.html`)

Interface en grimoire ouvert (deux colonnes). Page droite restructurée (2026-06-25) :

**3 onglets de saisie :**
1. **Deck Moxfield** (défaut) — liste déroulante des decks configurés dans `/deck-config`. Un clic charge la decklist via `GET /api/deck-txt/{id}` et révèle le commandant.
2. **Coller une liste** — textarea format Moxfield/Archidekt (`1 Card Name`), bouton "Charger cette liste".
3. **Fichier .txt** — drag & drop ou bouton "Parcourir un fichier".

**2 algorithmes disponibles :**
- **Analyse Populaire** (V1) — `POST /upload-deck` avec FormData
- **Deck Mentor IA** — `POST /api/deck/analyze` avec JSON

Dans tous les cas, le texte brut de la decklist est normalisé côté JS avant envoi.

### `/deck-config` — Gestion des decklists Moxfield

- Ajout par URL Moxfield
- Liste avec détection "Modifié localement" (pill orange)
- Bouton **Copier** : copie dans le presse-papiers + appel `mark-synced` + mise à jour UI inline
- Bouton **Mettre à jour** : refetch Moxfield (désactivé si modifié localement)
- Bouton **Supprimer** : désactivé jusqu'à ce que "Copier" ait été cliqué dans la session

### `/deck-build` — Ajouts depuis la collection

- Sélection d'un deck Moxfield depuis un dropdown
- Affichage des cartes en tuiles avec overlay +/− (hover)
- Clic + : ajoute la carte au .txt local (avec undo via toggle)
- Clic − : retire la carte du .txt local (avec undo)
- Barre de recherche manuelle (ajout/retrait par nom, autocomplete starts-with, badge % inclusion EDHREC)
- Noms de cartes cliquables → ouvre `/commander-suggest?card=NAME` dans un nouvel onglet

### `/commander-suggest` — Trouver un commandant

- Recherche par liste de cartes → commandants compatibles
- Paramètre URL `?card=NAME` : pré-remplit et lance la recherche automatiquement (utilisé depuis `deck-build`)

### `/deck-moves` — Déplacements entre decks

- Suggestions de transferts de cartes entre decks Moxfield configurés

### `/deck-trim` — Cartes à retirer

- Analyse des cartes du deck les moins populaires relativement au commandant

---

## 8. Dataset de decklists

### Structure

- **Dossier** : `data/Decklists/<Commandant>/` — un sous-dossier par commandant
- **Format CSV** : séparateur `;`, colonnes `Card Name`, `Quantity`, `Commander` (`YES`/`NO`)
- **Volume** : 36 443 decklists valides, 34 commandants, 25 130 cartes uniques

### Commandants disponibles (34)

Aesi, Atraxa, Brago, Captain N'ghathrod, Chulane, Edgar Markov, Eluge, Feather, Galadriel, Isshin, Kaalia, Korvold, Krenko, Kyler, Lathril, Meren, Miirym, Muldrotha, Nekusar, Omnath, Orah, Pantlaza, Prosper, Selvala, Shadowfax, Tatyova, Teysa Karlov, The Ur-Dragon, Urza Chief Artificer, Voja, Wilhelt, Yuriko, Zimone and Dina, Zurgo Helmsmasher

### Normalisation des commandants

- **Fichier** : `data/commander_aliases.json` (44 entrées → 34 noms canoniques)
- Mappe les variantes (underscores, apostrophes) vers le nom canonique
- Tout nom absent = parasite ignoré silencieusement
- **Ne jamais modifier** les noms de dossiers sans mettre à jour ce fichier
- Ajout d'un commandant : créer le sous-dossier dans `data/Decklists/` + entrée dans `commander_aliases.json` + relancer `compute_deck_stats.py` pour mettre à jour la DB

### Format decklist utilisateur (texte)

```
1 Elvish Mystic
1 Llanowar Elves
...

1 Lathril, Blade of the Elves
```

Règles : quantité + espace + nom, commandant = dernière section séparée par une ligne vide.

---

## 9. Scripts de statistiques

### `compute_deck_stats.py` — stats brutes

Calcule les statistiques d'inclusion depuis les CSV et les persiste en PostgreSQL. Streaming (une deck à la fois) — scalable à 100k+ decklists.

```powershell
uv run python scripts/compute_deck_stats.py              # DB uniquement (défaut)
uv run python scripts/compute_deck_stats.py --db-only    # DB seulement (explicite)
uv run python scripts/compute_deck_stats.py --top 200    # top 200 au lieu de 100
```

**Sortie** : écriture directe en base PostgreSQL — TRUNCATE + bulk INSERT par batches de 2 000.

Résultat actuel : 25 130 lignes dans `deck_stat_global`, 120 799 dans `deck_stat_commander`.

### `compute_commander_tfidf.py` — profils TF-IDF

Lit la DB et génère les profils TF-IDF par commandant. Dépend de `deck_stat_commander` et `deck_stat_global`.

```powershell
uv run python scripts/compute_commander_tfidf.py
```

**Formules :**
- `TF(card, commander) = inclusion_rate / 100`
- `IDF(card) = log(nb_commandants / nb_commandants_jouant_la_carte)` — déjà calculé dans `deck_stat_global`
- `TF-IDF = TF × IDF`
- `TF-IDF normalisé = TF-IDF / max(TF-IDF du commandant)` → score entre 0 et 1

**Sorties dans `data/stats/` :**

| Fichier | Contenu |
|---|---|
| `commander_tfidf.csv` | Toutes les 120 799 paires (commander, carte) avec scores |
| `commander_profiles/<slug>.csv` | Top 500 cartes TF-IDF par commandant |
| `commander_profiles_json/<slug>.json` | Top 20 cartes en JSON par commandant |
| `commander_summary.csv` | Résumé par commandant (deck_count, top_card, mean/max tfidf) |
| `commander_top_signatures.csv` | Top 20 cartes signatures pour les 34 commandants |

**Exemple — Galadriel, Light of Valinor (top 5) :**

| Carte | Inclusion | IDF | TF-IDF | Norm |
|---|---|---|---|---|
| Spara's Headquarters | 47.87% | 1.58 | 0.757 | 1.00 |
| Elrond, Master of Healing | 35.08% | 2.14 | 0.751 | 0.99 |
| Rejuvenating Springs | 60.40% | 1.22 | 0.739 | 0.98 |
| Galadhrim Brigade | 45.11% | 1.58 | 0.713 | 0.94 |
| Seaside Citadel | 36.81% | 1.92 | 0.706 | 0.93 |

> Sol Ring et les autres staples universels ont un IDF très faible → descendent dans le classement malgré un inclusion_rate élevé.

---

## 10. Script `build_card2vec.py` — Card2Vec

Entraîne un modèle Word2Vec sur les 36 443 decklists. Chaque deck = une phrase, chaque carte = un mot.

```powershell
uv run python scripts/build_card2vec.py
# ~5-10 min selon le CPU
```

### Hyperparamètres

| Paramètre | Valeur | Raison |
|---|---|---|
| `vector_size` | 128 | Compromis expressivité / taille |
| `window` | 10 | Large pour capturer la synergie globale d'un deck |
| `min_count` | 5 | Ignorer les cartes présentes dans moins de 5 decks |
| `sg` | 1 | Skip-Gram (meilleur sur vocabulaires avec mots rares) |
| `negative` | 10 | Negative sampling |
| `sample` | 1e-4 | Sous-échantillonnage des staples ultra-fréquents (Sol Ring…) |
| `epochs` | 10 | |

### Sorties dans `data/embeddings/`

| Fichier | Contenu |
|---|---|
| `card2vec.model` | Modèle Gensim complet (`Word2Vec.load(path)`) |
| `card_embeddings.npy` | Matrice float32 (16 040 × 128), triée alphabétiquement par token |
| `card_embeddings.csv` | card_name + 128 colonnes `v0`…`v127` |
| `card_index.json` | `{ card_name: index }` pour indexer `.npy` |
| `token_to_name.json` | `{ token_normalisé: nom_original }` — nécessaire pour l'affichage |
| `card_neighbors.csv` | 20 voisins cosinus par carte (16 040 × 20 lignes) |
| `commander_embeddings.npy` | Vecteurs commandants pondérés TF-IDF (34 × 128) |
| `commander_embeddings.json` | `{ commander: [float × 128], commander_to_index: {...} }` |

### Utilisation de `nearest_neighbors`

```python
from gensim.models import Word2Vec
import json

model = Word2Vec.load("data/embeddings/card2vec.model")
token_to_name = json.loads(open("data/embeddings/token_to_name.json").read())

# depuis scripts/build_card2vec.py
from scripts.build_card2vec import nearest_neighbors
neighbors = nearest_neighbors("Cultivate", model, token_to_name, top_n=10)
# → [("Kodama's Reach", 0.92), ("Farseek", 0.89), ...]
```

### Commander Embedding pondéré TF-IDF

```
v(commander) = Σ tfidf_norm(card, commander) × v(card)
               ─────────────────────────────────────────
                       Σ tfidf_norm(card, commander)
```

Produit un vecteur par commandant dans le même espace que les cartes — permet de calculer la proximité commandant ↔ carte sans entraînement supplémentaire.

### Pipeline complet (ordre d'exécution)

```powershell
# 1. Stats brutes → PostgreSQL
uv run python scripts/compute_deck_stats.py

# 2. Profils TF-IDF (lit la DB)
uv run python scripts/compute_commander_tfidf.py

# 3. Card2Vec + Commander Embeddings (lit les decklists + commander_tfidf.csv)
uv run python scripts/build_card2vec.py
```

---

## 11. Package MTG-DB

Les modèles SQLAlchemy et migrations Alembic sont dans un package Python séparé, partagé entre ManaMind et MTG-TRADE-FAB.

- **Repo** : https://github.com/Middel7/MTG-DB
- **Installation** : via `pyproject.toml` → `mtgdb @ git+https://github.com/Middel7/MTG-DB.git`

ManaMind ne définit **aucun modèle DB en propre**. Tous les fichiers `src/manamind/db/` sont des re-exports depuis `mtgdb`. Pour modifier le schéma : modifier dans MTG-DB, committer, pusher, puis `uv lock --upgrade-package mtgdb` dans ManaMind.

### Structure de mtgdb

```
MTG-DB/src/mtgdb/db/
├── base.py          ← DeclarativeBase (Base)
├── engine.py        ← SessionLocal, DATABASE_URL, get_db, check_connection
└── models/
    ├── card.py                      ← scryfall_cards
    ├── card_face.py                 ← scryfall_card_faces
    ├── card_printing.py             ← scryfall_card_printings
    ├── card_price.py                ← scryfall_card_prices
    ├── card_tag.py                  ← scryfall_card_tags
    ├── mtg_set.py                   ← scryfall_mtg_sets
    ├── import_run.py                ← import_runs
    └── deck_stats.py                ← deck_stat_global, deck_stat_commander
```

### Migrations Alembic

Gérées **exclusivement dans MTG-DB**. Chaîne actuelle :

```
392e971f (init) → ... → 20260620_add_scryfall_card_tags → 20260620_add_deck_stats_tables (HEAD)
```

```powershell
# Appliquer toutes les migrations (depuis MTG-DB)
cd c:\Users\fabie\Documents\GitHub\MTG-DB
alembic upgrade head
```

---

## 12. Base de données PostgreSQL

### Installation et configuration

**Option A — Installateur Windows**
1. Télécharger sur https://www.postgresql.org/download/windows/
2. Installer, retenir le mot de passe `postgres`

**Option B — Docker**
```powershell
docker run --name manamind-postgres `
  -e POSTGRES_USER=manamind `
  -e POSTGRES_PASSWORD=manamind `
  -e POSTGRES_DB=manamind `
  -p 5432:5432 -d postgres:16
```

**Créer la base et l'utilisateur :**
```sql
CREATE USER manamind WITH PASSWORD 'motdepasse';
CREATE DATABASE manamind OWNER manamind;
GRANT ALL PRIVILEGES ON DATABASE manamind TO manamind;
```

**Fichier `.env` à la racine :**
```
DATABASE_URL=postgresql://manamind:motdepasse@localhost:5432/manamind
```

Si `.env` est absent : `SessionLocal = None`, les routes `/api/cards/search` retournent HTTP 503.

**Vérifier la connexion :**
```powershell
uv run python -c "from src.manamind.db.engine import check_connection; print(check_connection())"
```

### Import des données Scryfall

```powershell
# Depuis le repo MTG-DB :
cd c:\Users\fabie\Documents\GitHub\MTG-DB
uv run python scripts/import_scryfall.py          # Import standard (~5-10 min)
uv run python scripts/import_scryfall.py --force  # Force re-téléchargement
uv run python scripts/import_scryfall.py --dry-run # Simulation sans DB
```

L'import est idempotent : `ON CONFLICT DO UPDATE` pour les cartes, `ON CONFLICT DO NOTHING` pour les prix.

**Volumes attendus après import :**

| Table | Lignes |
|---|---|
| `scryfall_cards` | ~30 000 |
| `scryfall_card_faces` | ~20 000 |
| `scryfall_card_printings` | ~30 000 |
| `scryfall_mtg_sets` | ~900 |
| `scryfall_card_prices` | ~90 000 |
| `deck_stat_global` | 25 130 |
| `deck_stat_commander` | 120 799 |

**Valider la base :**
```powershell
uv run python scripts/validate_mtg_cards_db.py
```

### Résolution des problèmes courants

| Problème | Solution |
|---|---|
| `could not connect to server` | Démarrer PostgreSQL ou le container Docker |
| `password authentication failed` | Vérifier `DATABASE_URL` dans `.env` |
| `database "manamind" does not exist` | Recréer la base (étape ci-dessus) |
| `ModuleNotFoundError: psycopg2` | `uv sync` |
| `DATABASE_URL absente` | Copier `.env.example` en `.env` |

---

## 13. Schéma de la base de données

```
scryfall_mtg_sets
  code (PK)
    ▲
    │ set_code (FK, SET NULL)
    │
scryfall_cards ◄────────────── scryfall_card_printings ◄──── scryfall_card_prices
  id (PK)                         id (PK)                       id (PK)
  oracle_id (UNIQUE)               scryfall_id (UNIQUE)          printing_id (FK)
  name / normalized_name           oracle_id                     source / currency
  mana_cost / mana_value           card_id (FK)                  price_type / price
  type_line / oracle_text          set_code (FK)                 date
  colors [] / color_identity []    rarity / image_normal
  keywords [] / legal_commander    scryfall_uri
  edhrec_rank / game_changer
    │
    └── scryfall_card_faces (cascade delete)
          face_name / mana_cost / oracle_text / image_normal

import_runs         (standalone — audit des imports Scryfall)
deck_stat_global    (standalone — fréquence globale des cartes)
deck_stat_commander (standalone — taux d'inclusion par commandant)
```

### `deck_stat_global`

| Colonne | Type | Description |
|---|---|---|
| `card_name` | TEXT UNIQUE | Nom de la carte |
| `decks_count` | BIGINT | Nb de decks distincts contenant la carte |
| `total_decks` | BIGINT | Nb total de decks dans le dataset |
| `global_frequency` | FLOAT | `decks_count / total_decks × 100` |
| `commanders_count` | INTEGER | Nb de commandants distincts jouant la carte |
| `idf` | FLOAT | `log(nb_commandants / commanders_count)` |
| `computed_at` | TIMESTAMPTZ | Date du dernier calcul |

### `deck_stat_commander`

| Colonne | Type | Description |
|---|---|---|
| `commander` | VARCHAR(255) | Nom canonique du commandant |
| `card_name` | TEXT | Nom de la carte |
| `decks_with_card` | INTEGER | Nb de decks de CE commandant contenant la carte |
| `total_decks` | INTEGER | Nb total de decks pour CE commandant |
| `inclusion_rate` | FLOAT | `decks_with_card / total_decks × 100` |
| `computed_at` | TIMESTAMPTZ | Date du dernier calcul |

Contrainte unique : `(commander, card_name)`.

### Requêtes types (SQLAlchemy)

```python
# Recherche de cartes par nom
stmt = select(Card).where(Card.name.ilike(f"%{query}%")).order_by(Card.edhrec_rank.asc().nulls_last()).limit(100)

# Prix EUR le plus récent
stmt = select(CardPrice.price).join(CardPrinting).where(
    CardPrinting.card_id == card_id,
    CardPrice.currency == "eur",
    CardPrice.price_type == "regular",
).order_by(CardPrice.date.desc()).limit(1)

# Cartes légales Commander d'une identité couleur
stmt = select(Card).where(
    Card.legal_commander == True,
    Card.color_identity.contained_by(["W", "U"]),
).order_by(Card.edhrec_rank.asc().nulls_last())
```

### Partage de la base entre projets

La base ne contient que des données MTG — aucune logique applicative propre à ManaMind. N'importe quel autre projet peut s'y connecter.

| Option | Quand |
|---|---|
| Python + SQLAlchemy | Accès complet, copier les modèles ou utiliser `MetaData.reflect()` |
| Autre langage | Connexion PostgreSQL native (attention aux colonnes `text[]`) |
| API REST | `GET /api/cards/search?q=...` si le serveur tourne |

---

## 14. Standards de développement

### Code

- Type hints obligatoires sur toutes les fonctions publiques
- Formatage : `ruff check --fix src/ && ruff format src/` (line-length=100)
- Logs informatifs à chaque étape importante (`import logging`)
- Un fichier = une responsabilité principale

### Sécurité

- `.env` jamais commité
- Encodage UTF-8 partout (`utf-8-sig` pour les fichiers Windows)
- Requêtes paramétrées SQLAlchemy (pas de concaténation SQL)

### Architecture DB

- Ne jamais modifier les modèles DB dans ManaMind → modifier dans MTG-DB
- Les migrations sont dans MTG-DB uniquement
- Ne jamais `pip install mtgdb` → utiliser la dépendance git dans `pyproject.toml`

---

## 15. Commandes utiles

```powershell
# Démarrer le serveur
uv run python server.py

# Calculer les stats de decklists (CSV + DB)
uv run python scripts/compute_deck_stats.py

# Valider la base de données
uv run python scripts/validate_mtg_cards_db.py

# Mettre à jour mtgdb après un push dans MTG-DB
uv lock --upgrade-package mtgdb
uv sync

# Appliquer les migrations Alembic (depuis MTG-DB)
cd c:\Users\fabie\Documents\GitHub\MTG-DB
alembic upgrade head

# Vérifier le linting
ruff check src/
ruff format src/
```

---

## 16. Historique des changements majeurs

### 2026-06-25

**Intégration Moxfield complète :**
- `moxfield_client.py` : ajout de `mark_as_synced()` — met à jour le mtime du cache JSON pour effacer l'état "modifié localement" sans refetch
- `server.py` : nouvel endpoint `POST /api/deck-txt/{id}/mark-synced`
- `/deck-config` : après clic "Copier", l'UI se met à jour inline (pill → Moxfield, boutons réactivés) sans rechargement

**Suppression de pages et routes :**
- `cards.html` supprimé — page de recherche de cartes par DB PostgreSQL
- `deck_suggest.html` supprimé — page de suggestions d'ajouts par détection de commandant
- Routes supprimées : `GET /cards`, `GET /deck-suggest`, `POST /api/deck-suggest`
- `suggest_additions()` dans `card_commander_matcher.py` n'est plus appelée
- Liens de nav retirés de : `deck_build.html`, `commander_suggest.html`, `deck_moves.html`, `deck_trim.html`, `deck_config.html`, `results.html`, `recommendations_view_slide16.html`
- `collection_suggest.html` avait déjà été supprimé (session précédente) ; `GET /api/collection-suggest` **conservé** car utilisé par `deck_build.html`

**Refonte de la page d'accueil (`recommendations_view_slide16.html`) :**
- Nouvelle zone de saisie avec 3 onglets : Deck Moxfield / Coller une liste / Fichier .txt
- Onglet Moxfield : charge `GET /api/moxfield-decks` et affiche les decks configurés
- Sélection d'un deck → `GET /api/deck-txt/{id}` → révélation du commandant
- Les 2 algos (Analyse Populaire + Deck Mentor IA) fonctionnent avec toutes les sources
- `pendingFile` remplacé par `pendingText` (texte brut normalisé), envoyé comme `Blob` en FormData pour V1

---

## 17. Priorités de développement

1. **Filtrage collaboratif** — implémenter `src/manamind/recommandation_collaborative.py` en lisant `deck_stat_commander` depuis PostgreSQL. Les données et profils TF-IDF sont prêts.
2. **Intégrer XGBoost dans le serveur** — charger `data/models/xgb_card2vec.json`. Card2Vec apporte +2.61% RMSE, +0.07 NDCG@20 sur Galadriel. Conserver `cosine_similarity`. Piste suivante : tags Scryfall et `game_changer`.
3. **Génération automatique des archétypes** — clustering sur `commander_tfidf.csv` (k-means ou HDBSCAN).
4. **Dataset** — ajouter de nouveaux commandants, refresh automatique depuis EDHREC.
5. **Interface** — tri par score, filtre par rôle, affichage mobile.
