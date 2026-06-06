# ManaMind AI — Documentation de la base de données

> **Stack** : PostgreSQL · SQLAlchemy 2.x (mapped_column) · Alembic  
> **Source des données** : Scryfall Bulk Data API  
> **Connexion** : variable d'environnement `DATABASE_URL` dans `.env`  
> **Migrations** : `392e971f7759` (init 2026-06-02) · `a1b2c3d4e5f6` (game_changer 2026-06-05)

---

## Partage entre projets

**Oui, c'est possible et recommandé.** La base ne contient que des données MTG (cartes, éditions, prix importés depuis Scryfall) — aucune logique applicative propre à ManaMind. N'importe quel autre projet peut se connecter avec la même `DATABASE_URL`.

### Trois options selon le contexte

| Option | Quand l'utiliser |
|---|---|
| **A — Connexion directe** (Python + SQLAlchemy) | Accès complet en lecture/écriture. Copie les modèles ou utilise `MetaData.reflect()`. |
| **B — Connexion directe** (autre langage) | Connexion PostgreSQL native. Attention aux colonnes `text[]` (ARRAY PostgreSQL). |
| **C — API REST ManaMind** | Si le serveur tourne (`GET /api/cards/search?q=…`). Pas besoin d'accès à la base. |

#### Option A — Python + SQLAlchemy

```python
from sqlalchemy import create_engine, MetaData
from sqlalchemy.orm import Session

engine = create_engine("postgresql://manamind:manamind@localhost:5432/manamind")

# Soit tu copies les modèles depuis src/manamind/db/models/
# Soit tu utilises reflect pour éviter de les copier :
meta = MetaData()
meta.reflect(bind=engine)
cards_table = meta.tables["cards"]

with Session(engine) as session:
    rows = session.execute(
        cards_table.select()
        .where(cards_table.c.legal_commander == True)
        .limit(10)
    ).fetchall()
```

> Les migrations Alembic restent gérées par ce repo. Si ton autre projet ajoute ses propres tables, configure un `version_table` différent dans son `alembic.ini` pour ne pas entrer en conflit.

#### Option B — Autre langage (Node.js, Go, etc.)

```
host=localhost port=5432 dbname=manamind user=manamind password=manamind
```

Point d'attention : `colors`, `color_identity` et `keywords` sont de type `text[]` (tableau PostgreSQL natif). La plupart des drivers les retournent sous forme de tableau ou de chaîne `{U,B}` à parser.

#### Option C — API REST

```
GET http://localhost:8000/api/cards/search?q=goblin&limit=100
```

Retourne jusqu'à 100 cartes avec `id`, `oracle_id`, `name`, `type_line`, `color_identity`, `edhrec_rank`.

---

## Vue d'ensemble

La base de données stocke toutes les cartes Magic: The Gathering à deux niveaux :

- **Niveau logique** (`cards`) : une carte = une entrée, quel que soit le nombre de réimpressions.  
- **Niveau physique** (`card_printings`) : une entrée par impression précise (édition, variante, langue).

```
mtg_sets ──────────────────────────┐
                                   │ set_code (FK, nullable)
cards ──── card_faces              │
  │                                │
  └── card_printings ──────────────┘
            │
            └── card_prices

import_runs  (table standalone, aucune FK)
```

---

## Tables

---

### `cards` — Cartes logiques (niveau oracle)

**Philosophie** : un `oracle_id` = une seule ligne, indépendamment du nombre d'impressions ou de reprints. Correspond au champ `oracle_id` de l'API Scryfall.

| Colonne | Type | Nullable | Index | Description |
|---|---|---|---|---|
| `id` | `INTEGER` | NON | PK | Clé primaire auto-incrémentée |
| `oracle_id` | `VARCHAR(36)` | NON | UNIQUE | UUID Scryfall stable entre reprints |
| `name` | `VARCHAR(255)` | NON | INDEX | Nom complet (ex: `"Fire // Ice"` pour les cartes split) |
| `normalized_name` | `VARCHAR(255)` | NON | INDEX | Nom en minuscules, sans accents, pour la recherche et le matching avec les decklists |
| `mana_cost` | `VARCHAR(100)` | OUI | — | Coût de mana (ex: `"{2}{U}{B}"`). Null pour les terrains et les faces verso de DFC |
| `mana_value` | `FLOAT` | OUI | — | Coût de mana converti (CMC). Float pour gérer les X et les demi-mana |
| `type_line` | `VARCHAR(255)` | OUI | — | Ligne de type complète (ex: `"Legendary Creature — Human Wizard"`) |
| `oracle_text` | `TEXT` | OUI | — | Texte des règles oracle |
| `power` | `VARCHAR(10)` | OUI | — | Force de créature (string car `"*"` ou `"2+1"` possible) |
| `toughness` | `VARCHAR(10)` | OUI | — | Endurance de créature |
| `loyalty` | `VARCHAR(10)` | OUI | — | Loyauté de planeswalker |
| `defense` | `VARCHAR(10)` | OUI | — | Défense de carte Bataille |
| `colors` | `VARCHAR[]` | OUI | — | Couleurs de la carte (ex: `["U", "B"]`). Array PostgreSQL natif |
| `color_identity` | `VARCHAR[]` | OUI | — | Identité de couleur Commander (inclut les capacités activées et les symboles de mana dans le texte) |
| `keywords` | `VARCHAR[]` | OUI | — | Mots-clés de règles (ex: `["Flying", "Deathtouch"]`) |
| `legal_commander` | `BOOLEAN` | NON | — | `true` si la carte est légale en format Commander |
| `edhrec_rank` | `INTEGER` | OUI | — | Classement EDHREC (1 = plus populaire, null si absent de EDHREC) |
| `game_changer` | `BOOLEAN` | NON | INDEX | `true` si la carte est classée "Game Changer" par Scryfall (cartes à fort impact sur le jeu) |
| `created_at` | `TIMESTAMPTZ` | NON | — | Date de première insertion (géré par `server_default`) |
| `updated_at` | `TIMESTAMPTZ` | NON | — | Date de dernière mise à jour (géré par `onupdate`) |

**Relations**
- `faces` → `card_faces` (1-N, cascade delete)
- `printings` → `card_printings` (1-N)

**Valeurs possibles pour `colors` / `color_identity`**

| Code | Couleur |
|---|---|
| `W` | Blanc |
| `U` | Bleu |
| `B` | Noir |
| `R` | Rouge |
| `G` | Vert |

Exemples : `[]` = incolore, `["W","U"]` = Azorius, `["W","U","B","R","G"]` = WUBRG

---

### `card_faces` — Faces de cartes double-face / split

**Philosophie** : seulement créées quand le JSON Scryfall contient un tableau `card_faces`. Supprimées et réinsérées à chaque import (pas d'identifiant stable côté Scryfall pour les faces).

Exemples de cartes concernées : `Delver of Secrets // Insectile Aberration`, `Fire // Ice`, `Murderous Rider // Swift End`.

| Colonne | Type | Nullable | Index | Description |
|---|---|---|---|---|
| `id` | `INTEGER` | NON | PK | Clé primaire |
| `card_id` | `INTEGER` | NON | INDEX | FK → `cards.id` (cascade delete) |
| `face_name` | `VARCHAR(255)` | NON | — | Nom de cette face (ex: `"Delver of Secrets"`) |
| `mana_cost` | `VARCHAR(100)` | OUI | — | Coût de mana de cette face (null sur la face verso) |
| `type_line` | `VARCHAR(255)` | OUI | — | Ligne de type de cette face |
| `oracle_text` | `TEXT` | OUI | — | Texte des règles de cette face |
| `power` | `VARCHAR(10)` | OUI | — | Force |
| `toughness` | `VARCHAR(10)` | OUI | — | Endurance |
| `loyalty` | `VARCHAR(10)` | OUI | — | Loyauté |
| `defense` | `VARCHAR(10)` | OUI | — | Défense |
| `colors` | `VARCHAR[]` | OUI | — | Couleurs propres à cette face |
| `image_small` | `TEXT` | OUI | — | URL image Scryfall petite résolution |
| `image_normal` | `TEXT` | OUI | — | URL image Scryfall résolution normale |
| `image_large` | `TEXT` | OUI | — | URL image Scryfall grande résolution |

---

### `card_printings` — Impressions physiques

**Philosophie** : un `scryfall_id` = une impression précise (édition + numéro de collection + langue). Un `oracle_id` peut avoir des dizaines de printings (reprints, collector boosters, promo, foil, etc.).

| Colonne | Type | Nullable | Index | Description |
|---|---|---|---|---|
| `id` | `INTEGER` | NON | PK | Clé primaire |
| `scryfall_id` | `VARCHAR(36)` | NON | UNIQUE | UUID Scryfall de cette impression précise |
| `oracle_id` | `VARCHAR(36)` | NON | INDEX | UUID oracle dupliqué (évite un JOIN pour les requêtes fréquentes) |
| `card_id` | `INTEGER` | NON | INDEX | FK → `cards.id` (cascade delete) |
| `set_code` | `VARCHAR(16)` | OUI | INDEX | FK → `mtg_sets.code` (SET NULL si set absent). Ex: `"mkm"`, `"bro"` |
| `collector_number` | `VARCHAR(20)` | OUI | — | Numéro dans l'édition (ex: `"023"`, `"023★"` pour variantes) |
| `lang` | `VARCHAR(10)` | OUI | — | Langue (ex: `"en"`, `"fr"`, `"ja"`) |
| `rarity` | `VARCHAR(20)` | OUI | — | `"common"`, `"uncommon"`, `"rare"`, `"mythic"`, `"special"`, `"bonus"` |
| `released_at` | `DATE` | OUI | — | Date de sortie de cette impression |
| `artist` | `VARCHAR(255)` | OUI | — | Nom de l'illustrateur |
| `border_color` | `VARCHAR(20)` | OUI | — | Couleur de la bordure (ex: `"black"`, `"white"`, `"borderless"`) |
| `frame` | `VARCHAR(20)` | OUI | — | Génération du cadre (ex: `"2015"`, `"extendedart"`) |
| `full_art` | `BOOLEAN` | NON | — | True si la carte est full-art |
| `promo` | `BOOLEAN` | NON | — | True si c'est une carte promo |
| `reprint` | `BOOLEAN` | NON | — | True si c'est un reprint (pas la première impression) |
| `digital` | `BOOLEAN` | NON | — | True si c'est une version digitale (Magic Online, Arena) |
| `image_small` | `TEXT` | OUI | — | URL image petite résolution |
| `image_normal` | `TEXT` | OUI | — | URL image normale (488×680 px) — utilisée dans l'interface |
| `image_large` | `TEXT` | OUI | — | URL image grande résolution |
| `scryfall_uri` | `TEXT` | OUI | — | URL de la page Scryfall de cette impression |

**Relations**
- `card` → `cards` (N-1)
- `mtg_set` → `mtg_sets` (N-1, nullable)
- `prices` → `card_prices` (1-N, cascade delete)

---

### `card_prices` — Historique des prix

**Philosophie** : table **append-only**. On n'écrase jamais un prix passé : chaque import insère une nouvelle ligne. Permet de suivre l'évolution des prix dans le temps. Contrainte unique `(printing_id, date, source, currency, price_type)` pour l'idempotence (`ON CONFLICT DO NOTHING`).

| Colonne | Type | Nullable | Index | Description |
|---|---|---|---|---|
| `id` | `INTEGER` | NON | PK | Clé primaire |
| `printing_id` | `INTEGER` | NON | INDEX | FK → `card_printings.id` (cascade delete) |
| `source` | `VARCHAR(50)` | NON | — | Source du prix : `"scryfall"` (prévu : `"cardmarket"`, `"tcgplayer"`) |
| `currency` | `VARCHAR(3)` | NON | — | `"eur"`, `"usd"`, `"tix"` (Magic Online tickets) |
| `price_type` | `VARCHAR(20)` | NON | — | `"regular"`, `"foil"`, `"etched"` |
| `price` | `NUMERIC(10,2)` | OUI | — | Prix avec 2 décimales. Null si Scryfall ne connaît pas le prix |
| `date` | `DATE` | NON | INDEX | Date de collecte (date de l'import) |

**Contrainte unique** : `uq_card_prices_printing_date_type` sur `(printing_id, date, source, currency, price_type)`

---

### `mtg_sets` — Éditions MTG

| Colonne | Type | Nullable | Index | Description |
|---|---|---|---|---|
| `id` | `INTEGER` | NON | PK | Clé primaire |
| `code` | `VARCHAR(16)` | NON | UNIQUE | Code court de l'édition (ex: `"bro"`, `"ltr"`, `"mkm"`) |
| `name` | `VARCHAR(255)` | NON | — | Nom complet (ex: `"The Brothers' War"`) |
| `set_type` | `VARCHAR(50)` | OUI | — | Type : `"expansion"`, `"core"`, `"commander"`, `"masters"`, `"promo"`, etc. |
| `released_at` | `DATE` | OUI | — | Date de sortie |
| `block` | `VARCHAR(100)` | OUI | — | Bloc d'appartenance (null pour les sets modernes) |
| `parent_set_code` | `VARCHAR(16)` | OUI | — | Code du set parent (ex: les Commander decks pointent vers leur set principal) |
| `card_count` | `INTEGER` | OUI | — | Nombre de cartes dans l'édition |
| `icon_svg_uri` | `TEXT` | OUI | — | URL du SVG de l'icône sur Scryfall |

---

### `import_runs` — Traçabilité des imports

Table standalone (aucune FK sortante). Enregistre chaque exécution du script d'import pour monitoring et audit.

| Colonne | Type | Nullable | Description |
|---|---|---|---|
| `id` | `INTEGER` | NON | Clé primaire |
| `source` | `VARCHAR(50)` | NON | Source : `"scryfall"` (prévu : `"mtgjson"`) |
| `source_file` | `TEXT` | OUI | URL ou chemin du fichier téléchargé |
| `source_updated_at` | `TIMESTAMPTZ` | OUI | Date `updated_at` retournée par l'API source |
| `started_at` | `TIMESTAMPTZ` | NON | Début d'import (`server_default = now()`) |
| `finished_at` | `TIMESTAMPTZ` | OUI | Fin d'import (null si import en cours ou échoué avant fin) |
| `status` | `VARCHAR(20)` | NON | `"running"` → `"success"` ou `"failed"` |
| `cards_imported` | `INTEGER` | NON | Nombre de cartes insérées/mises à jour |
| `printings_imported` | `INTEGER` | NON | Nombre d'impressions insérées/mises à jour |
| `errors_count` | `INTEGER` | NON | Nombre d'erreurs non-bloquantes |
| `error_message` | `TEXT` | OUI | Message d'erreur complet si `status = "failed"` |

---

## Schéma relationnel complet

```
mtg_sets
  code PK
    ▲
    │ set_code (FK, SET NULL)
    │
cards ◄──────────────────── card_printings ◄──── card_prices
  id PK                        id PK                id PK
  oracle_id (UNIQUE)            scryfall_id (UNIQUE) printing_id (FK)
  name                          oracle_id            source
  normalized_name               card_id (FK)         currency
  mana_cost                     set_code (FK)        price_type
  mana_value                    rarity               price
  type_line                     image_normal         date
  oracle_text                   scryfall_uri
  colors []
  color_identity []
  keywords []
  legal_commander
  edhrec_rank
  game_changer
    │
    └── card_faces
          id PK
          card_id (FK)
          face_name
          mana_cost
          oracle_text
          image_normal

import_runs (standalone)
  id PK
  source
  status
  cards_imported
  printings_imported
```

---

## Import des données

### Script : `scripts/import_scryfall_cards.py`

```bash
# Import standard
python scripts/import_scryfall_cards.py

# Forcer le re-téléchargement même si le fichier existe déjà
python scripts/import_scryfall_cards.py --force

# Mode simulation : parse et compte sans toucher la base
python scripts/import_scryfall_cards.py --dry-run
```

**Flux d'import :**
1. `GET https://api.scryfall.com/bulk-data` → récupère l'URL du fichier `default_cards`
2. Téléchargement → `data/raw/scryfall/<filename>`
3. `GET https://api.scryfall.com/sets` → upsert dans `mtg_sets` (FK obligatoire avant les cartes)
4. Parsing streaming `ijson` → batches de 500 cartes : `cards` / `card_faces` / `card_printings` / `card_prices`
5. Mise à jour de `import_runs` (début, fin, compteurs, erreurs)

---

## Connexion et configuration

**Fichier** : `.env` à la racine du projet

```env
DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/manamind
```

**Comportement si `DATABASE_URL` est absent** : `SessionLocal = None`, les routes API retournent HTTP 503.

**Migrations Alembic** :

```bash
# Appliquer toutes les migrations
alembic upgrade head

# Créer une nouvelle migration après modification d'un modèle
alembic revision --autogenerate -m "description"

# Voir l'état actuel
alembic current
```

---

## Requêtes types (SQLAlchemy)

### Recherche de cartes par nom (partielle, insensible à la casse)

```python
stmt = (
    select(Card)
    .where(Card.name.ilike(f"%{query}%"))
    .order_by(Card.edhrec_rank.asc().nulls_last(), Card.name)
    .limit(100)
)
```

### Prix EUR le plus récent d'une carte

```python
stmt = (
    select(CardPrice.price)
    .join(CardPrinting, CardPrice.printing_id == CardPrinting.id)
    .where(
        CardPrinting.card_id == card_id,
        CardPrice.currency == "eur",
        CardPrice.price_type == "regular",
    )
    .order_by(CardPrice.date.desc())
    .limit(1)
)
```

### Image normale de la première impression d'une carte

```python
# Sous-requête : ID de la première impression par carte
first_printing = (
    select(CardPrinting.card_id, func.min(CardPrinting.id).label("pid"))
    .group_by(CardPrinting.card_id)
    .subquery()
)
PrintingAlias = aliased(CardPrinting)

stmt = (
    select(Card, PrintingAlias.image_normal, PrintingAlias.scryfall_uri)
    .outerjoin(first_printing, Card.id == first_printing.c.card_id)
    .outerjoin(PrintingAlias, PrintingAlias.id == first_printing.c.pid)
    .where(Card.id == card_id)
)
```

### Toutes les cartes légales en Commander d'une identité de couleur

```python
stmt = (
    select(Card)
    .where(
        Card.legal_commander == True,
        Card.color_identity.contained_by(["W", "U"]),  # Azorius ou moins
    )
    .order_by(Card.edhrec_rank.asc().nulls_last())
)
```

---

## Notes importantes

| Point | Détail |
|---|---|
| **Séparation logique / physique** | `cards` = identité oracle stable. `card_printings` = version physique avec image et prix. Ne jamais stocker les images dans `cards`. |
| **Recherche par nom** | Utiliser `normalized_name` (minuscules, sans accents) pour les comparaisons avec les decklists texte. Utiliser `ilike` sur `name` pour la recherche utilisateur. |
| **Prix** | La table `card_prices` est append-only. Pour le prix actuel, prendre `max(date)` ou `order by date desc limit 1`. |
| **Cartes double-face** | `image_uris` est null sur la carte parente pour les DFC → utiliser `card_faces[0].image_normal`. L'API Scryfall retourne les deux faces dans `card_faces`. |
| **`oracle_id` dans `card_printings`** | Dupliqué intentionnellement pour éviter un JOIN `card_printings → cards` dans les requêtes fréquentes. |
| **Cascades** | `cards` supprimé → `card_faces` + `card_printings` supprimés. `card_printings` supprimé → `card_prices` supprimées. `mtg_sets` supprimé → `set_code` mis à null dans `card_printings`. |
