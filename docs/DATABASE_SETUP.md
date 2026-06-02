# Guide d'installation — Base de données ManaMind AI

Ce guide explique comment installer PostgreSQL, configurer la base et appliquer les migrations.

---

## Prérequis

- Python 3.12+ avec `pip` ou `uv`
- PostgreSQL 15+ (installation ci-dessous)
- Les dépendances Python installées (`pip install -r` ou `uv sync`)

---

## 1. Installer PostgreSQL

### Option A — Installateur Windows (recommandé)

1. Télécharger depuis https://www.postgresql.org/download/windows/
2. Lancer l'installateur, retenir le mot de passe du superutilisateur `postgres`
3. Laisser le port par défaut : **5432**
4. Après installation, ouvrir **pgAdmin** ou **psql** pour vérifier

### Option B — Docker (si Docker Desktop est installé)

```powershell
docker run --name manamind-postgres `
  -e POSTGRES_USER=manamind `
  -e POSTGRES_PASSWORD=manamind `
  -e POSTGRES_DB=manamind `
  -p 5432:5432 `
  -d postgres:16
```

Vérification :
```powershell
docker ps   # doit afficher manamind-postgres en running
```

---

## 2. Créer la base de données

### Via psql (ligne de commande)

```powershell
# Se connecter en tant que superutilisateur
psql -U postgres -h localhost

# Dans psql :
CREATE USER manamind WITH PASSWORD 'motdepasse';
CREATE DATABASE manamind OWNER manamind;
GRANT ALL PRIVILEGES ON DATABASE manamind TO manamind;
\q
```

### Via pgAdmin (interface graphique)

1. Ouvrir pgAdmin → Servers → PostgreSQL
2. Clic droit sur "Databases" → Create → Database
3. Nom : `manamind`, Owner : `manamind`

---

## 3. Configurer le fichier `.env`

```powershell
# À la racine du projet
copy .env.example .env
```

Éditer `.env` :
```
DATABASE_URL=postgresql://manamind:motdepasse@localhost:5432/manamind
```

Adapter `manamind`, `motdepasse` selon ce que tu as configuré à l'étape 2.

---

## 4. Installer les dépendances Python

```powershell
# Avec pip (Python 3.13 installé)
pip install sqlalchemy alembic psycopg2-binary python-dotenv httpx ijson tqdm

# Ou quand uv est disponible :
# uv sync
```

---

## 5. Vérifier la connexion

```powershell
python -c "from src.manamind.db.engine import check_connection; print(check_connection())"
```

Résultat attendu : `True`

---

## 6. Générer et appliquer la migration initiale

```powershell
# Générer le fichier de migration depuis les modèles Python
alembic revision --autogenerate -m "init"

# Appliquer la migration (crée toutes les tables)
alembic upgrade head
```

Alembic créera un fichier dans `alembic/versions/` avec toutes les instructions SQL.

> **Important** : ne jamais lancer `alembic downgrade` en production. Les migrations sont non-destructives.

---

## 7. Vérifier que les tables sont créées

```powershell
# Via psql
psql -U manamind -d manamind -h localhost -c "\dt"
```

Tables attendues :
```
 Schema |     Name       | Type  |   Owner
--------+----------------+-------+----------
 public | alembic_version| table | manamind
 public | card_faces     | table | manamind
 public | card_prices    | table | manamind
 public | card_printings | table | manamind
 public | cards          | table | manamind
 public | import_runs    | table | manamind
 public | mtg_sets       | table | manamind
```

---

## 8. Importer les données Scryfall

```powershell
# Import complet (télécharge + insère ~30 000 cartes)
python scripts/import_scryfall_cards.py

# Retélécharger le fichier même s'il est déjà présent localement
python scripts/import_scryfall_cards.py --force

# Tester sans toucher la base (parse + compte uniquement)
python scripts/import_scryfall_cards.py --dry-run
```

L'import est **relançable sans créer de doublons** : toutes les insertions
utilisent `ON CONFLICT DO UPDATE` (cartes, impressions, éditions) ou
`ON CONFLICT DO NOTHING` (prix).

**Durée estimée** :
| Étape | Durée approximative |
|---|---|
| Téléchargement (~50 Mo) | 30–90 s selon la connexion |
| Import des éditions (~1 000) | < 5 s |
| Import des cartes (~30 000) | 3–8 min |
| **Total** | **~5–10 min** |

## 9. Vérifier l'import

```powershell
# Compter les lignes par table
psql -U manamind -d manamind -h localhost -c "
  SELECT 'cards' AS t, COUNT(*) FROM cards
  UNION ALL SELECT 'card_faces', COUNT(*) FROM card_faces
  UNION ALL SELECT 'card_printings', COUNT(*) FROM card_printings
  UNION ALL SELECT 'mtg_sets', COUNT(*) FROM mtg_sets
  UNION ALL SELECT 'card_prices', COUNT(*) FROM card_prices
  UNION ALL SELECT 'import_runs', COUNT(*) FROM import_runs;
"
```

Résultats attendus après un import complet :
```
     t          | count
----------------+-------
 cards          | ~30 000
 card_faces     | ~20 000  (cartes double-face uniquement)
 card_printings | ~30 000
 mtg_sets       | ~900
 card_prices    | ~90 000  (2–3 prix par carte)
 import_runs    |       1
```

```powershell
# Vérifier que le run s'est bien terminé
psql -U manamind -d manamind -h localhost -c "
  SELECT id, status, cards_imported, errors_count, 
         EXTRACT(EPOCH FROM (finished_at - started_at))::int AS duree_s
  FROM import_runs ORDER BY id DESC LIMIT 5;
"
```

```powershell
# Chercher une carte par nom normalisé
psql -U manamind -d manamind -h localhost -c "
  SELECT name, mana_cost, type_line, legal_commander 
  FROM cards WHERE normalized_name = 'sol ring';
"

## 10. Valider la base avec le script dédié

```powershell
python scripts/validate_mtg_cards_db.py
```

Affiche :
- Statistiques globales (cartes, impressions, sets, prix)
- Couverture images et prix
- Statut du dernier import
- Vérification de 10 cartes de référence (Sol Ring, Counterspell, etc.)

Retourne le code de sortie `0` si tout est OK, `1` si des anomalies sont détectées.
```

---

## Résolution des problèmes courants

| Problème | Cause probable | Solution |
|---|---|---|
| `could not connect to server` | PostgreSQL non démarré | Démarrer le service Windows ou le container Docker |
| `password authentication failed` | Mauvais mot de passe dans .env | Vérifier DATABASE_URL dans .env |
| `database "manamind" does not exist` | Base non créée | Refaire l'étape 2 |
| `ModuleNotFoundError: psycopg2` | Dépendances non installées | `pip install psycopg2-binary` |
| `RuntimeError: La variable DATABASE_URL est absente` | Fichier .env manquant | Copier .env.example en .env |

---

## Structure des tables

```
mtg_sets ←─────────────── card_printings ──────────→ cards ──→ card_faces
                                │
                                └──→ card_prices

import_runs (standalone — audit d'import)
```

---

*Dernière mise à jour : 2026-06-02*
