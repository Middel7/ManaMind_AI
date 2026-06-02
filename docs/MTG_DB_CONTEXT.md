# Contexte technique — Base de données MTG (ManaMind AI)

> Fichier de référence pour l'assistant de développement.
> Mis à jour à chaque changement majeur d'architecture.

---

## 1. État actuel du projet (analyse du 2026-06-02)

### Stack existante (Python — à conserver)
| Composant | Détail |
|---|---|
| Langage | Python 3.13 installé (pyproject.toml cible 3.12.8) |
| Package manager | `uv` (non disponible en PATH) / `pip` utilisé en fallback |
| Framework web | FastAPI + Uvicorn (`server.py`) |
| Serveur legacy | WSGI stdlib (`recommendations_server.py` port 8000) |
| Frontend | HTML/CSS/JS vanilla (`recommendations_view_slide16.html`) |
| ORM / DB | Aucun — fichiers CSV uniquement |
| Linter/formateur | Ruff |

### Stack cible à ajouter (Python — même stack)
| Composant | Choix |
|---|---|
| Base de données | PostgreSQL 15+ |
| ORM | SQLAlchemy 2.0 |
| Migrations | Alembic |
| Driver PostgreSQL | psycopg2-binary |
| HTTP client | httpx |
| Parsing JSON stream | ijson (indispensable pour ~1 Go) |
| Progression terminal | tqdm |
| Env variables | python-dotenv |
| Scripts d'import | `scripts/` à la racine |

> Décision du 2026-06-02 : Node.js / Prisma abandonnés au profit de la stack Python existante.

---

## 2. Structure des dossiers actuels

```
ManaMind_AI/
├── data/                        # gitignored — données locales
│   ├── Decklists/               # ~25 208 decks par commandant (CSV ; séparé)
│   ├── cards_unique_global.csv  # stats globales des cartes (25 208 decks)
│   ├── My decks/                # decklists personnelles (.txt)
│   ├── User decklist/           # decks uploadés via l'UI
│   └── User recommendations/   # CSV de recommandations générés
├── docs/                        # documentation
│   ├── PROJECT_DOCUMENTATION.md # doc centrale du projet
│   ├── AGENTS.md                # règles pour l'agent IA
│   └── MTG_DB_CONTEXT.md       # CE FICHIER
├── outputs/                     # CSV générés par server.py
├── src/manamind/                # code Python
│   ├── recommend_deck_changes.py
│   └── refresh_ppt_summary.py
├── uploads/                     # uploads temporaires
├── card_database_final.csv      # base de cartes (format , — 7 778+ cartes)
├── recommendations_view_slide16.html
├── server.py                    # serveur FastAPI (port 8000)
├── recommendations_server.py    # serveur WSGI legacy (port 8000)
├── pyproject.toml
└── uv.lock
```

### Dossiers créés en Phase 1
```
ManaMind_AI/
├── src/manamind/db/
│   ├── base.py                  # DeclarativeBase SQLAlchemy
│   ├── engine.py                # create_engine, SessionLocal, get_db
│   └── models/
│       ├── card.py              # Table cards + normalize_card_name()
│       ├── card_face.py         # Table card_faces
│       ├── card_printing.py     # Table card_printings
│       ├── mtg_set.py           # Table mtg_sets
│       ├── card_price.py        # Table card_prices
│       └── import_run.py        # Table import_runs
├── alembic/                     # Migrations Alembic
│   ├── env.py
│   ├── script.py.mako
│   └── versions/                # Fichiers générés par alembic revision
├── alembic.ini
├── .env                         # DATABASE_URL (gitignored)
└── .env.example                 # Template à copier
```

---

## 3. Données existantes

### card_database_final.csv
- Format : virgule `,`
- Colonnes : `Card Name, Decks avec carte, Decks totaux analysés, % apparition dans les decks, Quantité totale, Présente comme commander dans X decks, Nombre de commanders concernés`
- Contenu : ~7 778 cartes uniques issues des 25 208 decks analysés
- **Limitation** : pas de données Scryfall (pas d'oracle_id, mana_cost, type_line, image_uri…)

### data/cards_unique_global.csv
- Format : point-virgule `;`
- Mêmes colonnes + colonne `Commanders concernés` (liste séparée par `|`)
- Source : calculé à partir des decklists locales

### data/Decklists/
- Un sous-dossier par commandant (ex: `Captain_Nghathrod/`)
- Chaque fichier = un deck au format CSV avec colonnes `Card Name`, `Commander`

---

## 4. Source de données Scryfall

- **URL bulk data** : `https://data.scryfall.io/default-cards/default-cards-YYYYMMDD.json`
- **Endpoint catalog** : `https://api.scryfall.com/bulk-data` (liste les fichiers disponibles)
- **Format** : JSON, ~300 Mo compressé, ~1 Go décompressé
- **Champs utiles** : `id` (UUID), `oracle_id`, `name`, `mana_cost`, `cmc`, `type_line`, `oracle_text`, `colors`, `color_identity`, `legalities`, `image_uris`, `prices`, `set`, `rarity`
- **Fréquence de mise à jour** : quotidienne

---

## 5. Schéma de base de données cible (MVP)

### Table `Card`
| Colonne | Type | Description |
|---|---|---|
| id | String (UUID) | Scryfall card ID |
| oracle_id | String | ID unique par carte (ignore les reprints) |
| name | String | Nom de la carte |
| mana_cost | String? | ex: `{2}{U}{B}` |
| cmc | Float | Coût de mana converti |
| type_line | String | ex: `Legendary Creature — Human Wizard` |
| oracle_text | String? | Texte règles |
| colors | String[] | `["U", "B"]` |
| color_identity | String[] | pour Commander |
| rarity | String | common/uncommon/rare/mythic |
| set_code | String | ex: `bro` |
| image_uri | String? | URL image Scryfall |
| price_eur | Float? | Prix Cardmarket |
| is_commander_legal | Boolean | légalité format Commander |
| created_at | DateTime | |

### Tables futures (Phase 2)
- `Commander` — commandants connus du dataset
- `Deck` — decks importés
- `DeckCard` — relation Deck ↔ Card avec quantité
- `Recommendation` — résultats du moteur de reco

---

## 6. Fichiers créés / modifiés (Phase 1)

### Créés
| Fichier | Rôle |
|---|---|
| `src/manamind/db/base.py` | DeclarativeBase SQLAlchemy |
| `src/manamind/db/engine.py` | Connexion, SessionLocal, check_connection() |
| `src/manamind/db/models/card.py` | Modèle Card + normalize_card_name() |
| `src/manamind/db/models/card_face.py` | Modèle CardFace |
| `src/manamind/db/models/card_printing.py` | Modèle CardPrinting |
| `src/manamind/db/models/mtg_set.py` | Modèle MtgSet |
| `src/manamind/db/models/card_price.py` | Modèle CardPrice |
| `src/manamind/db/models/import_run.py` | Modèle ImportRun |
| `alembic.ini` | Configuration Alembic |
| `alembic/env.py` | Env Alembic (charge .env, importe Base) |
| `alembic/script.py.mako` | Template des fichiers de migration |
| `.env.example` | Template de configuration |
| `docs/DATABASE_SETUP.md` | Guide d'installation complet |

### Modifiés
| Fichier | Modification |
|---|---|
| `pyproject.toml` | Ajout sqlalchemy, alembic, psycopg2-binary, httpx, ijson, tqdm, python-dotenv |
| `.gitignore` | Ajout `.env` et `data/raw/` |

---

## 7. Commandes à lancer (Phase 1)

```powershell
# 1. Installer les dépendances Python
pip install sqlalchemy alembic psycopg2-binary python-dotenv httpx ijson tqdm

# 2. Créer le fichier .env depuis le template
copy .env.example .env
# Éditer .env avec ton DATABASE_URL

# 3. Créer la base PostgreSQL (voir docs/DATABASE_SETUP.md étape 2)

# 4. Vérifier la connexion
python -c "from src.manamind.db.engine import check_connection; print(check_connection())"

# 5. Générer le fichier de migration initial
alembic revision --autogenerate -m "init"

# 6. Appliquer la migration (crée les 6 tables)
alembic upgrade head

# 7. Vérifier les tables créées
psql -U manamind -d manamind -h localhost -c "\dt"
```

---

## 8. Décisions techniques et justifications

| Décision | Raison |
|---|---|
| SQLAlchemy 2.0 + Alembic | Stack Python native, migrations robustes, typage fort avec Mapped[] |
| Pas de Node.js / Prisma | MVP — évite d'introduire une deuxième stack technologique |
| Scryfall bulk plutôt qu'API card par card | ~30 000 cartes en 1 requête, pas de rate-limit |
| `oracle_id` comme clé métier | Évite les doublons entre reprints d'une même carte |
| `ijson` pour le parsing | Le JSON Scryfall fait ~1 Go — ijson parse en streaming sans tout charger en RAM |
| `card_faces` en cascade delete | Les faces n'ont pas de clé stable dans Scryfall → réinsertion à chaque import |
| `card_prices` append-only | Historique des prix conservé, jamais d'écrasement |

---

## 9. Contraintes à respecter

- Ne **pas** supprimer `data/`, `src/`, `server.py`, `recommendations_server.py`
- Ne **pas** casser le flux existant Python → CSV → HTML
- `data/` est gitignored → ne pas y mettre de code
- Toujours utiliser `uv` pour Python (quand disponible) et `npm` pour Node.js
- Les migrations Prisma sont **non-destructives** sur les données existantes

---

*Dernière mise à jour : 2026-06-02*
