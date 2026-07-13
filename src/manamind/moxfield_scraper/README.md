# moxfield_scraper

Scrape moxfield.com et alimente la base. Remplace la chaîne externe
« Mox scrapper → CSV sur le Bureau → `scripts/import_deck_cards.py` » :
le HTML transite en mémoire, il n'y a plus aucun fichier intermédiaire.

## Zone autonome — ne touche à rien d'existant

Le package écrit dans **ses propres tables**, préfixées `mox_` :

| Table | Contenu |
|---|---|
| `mox_decks` | un deck : commander, deck_type, bracket, price, dates, `scraped_at` |
| `mox_deck_cards` | une carte par ligne : `(deck_id, card_name)`, quantity, is_commander |
| `mox_commanders` | liste ordonnée + état de reprise (`last_scraped_at`, `decks_extracted`) |

Elles vivent dans un `MetaData` séparé de `mtgdb.db.base.Base`, et ne peuvent
donc **pas** écraser la table `deck_cards` de ManaMind (schéma différent : pas de
quantité, `commander` porté par chaque ligne). L'intégration aux tables ManaMind,
si tu la veux, se fait en réécrivant `db.upsert_decks()` — le reste du package
n'a pas à bouger, parce que `parse_deck_html()` retourne un `Deck` (dataclass
neutre) et pas des lignes SQL.

## Utilisation

Commandes à lancer avec le venv activé (`.venv/Scripts/activate`).

Note : `uv run` échoue sur ce projet, indépendamment du scraper — `umap-learn`
tire `numba` 0.53.1, qui ne compile pas sur Python 3.12. Utiliser le python du
venv directement (`.venv\Scripts\python.exe`).

```bash
playwright install chromium        # une fois par machine — le navigateur

python scripts/scrape_moxfield.py init-db
python scripts/scrape_moxfield.py import-commanders data/TOPCOMMANDER.csv
python scripts/scrape_moxfield.py top --limit 200          # N decks par commandant
python scripts/scrape_moxfield.py commander "The Ur-Dragon" --limit 500
python scripts/scrape_moxfield.py recent --limit 1000      # tous commandants
```

`DATABASE_URL` est lue dans le `.env` du projet. `--db` permet de la surcharger
(pratique pour un essai sur `sqlite:///test.db` sans toucher au Postgres).

Depuis du code :

```python
from manamind.moxfield_scraper import init_schema, make_engine, scrape

engine = make_engine(os.environ["DATABASE_URL"])
init_schema(engine)
stats = scrape(engine, commander="The Ur-Dragon", limit=500)
```

## La base porte l'état

Il n'y a pas de fichier de reprise. Un `Ctrl-C` puis un relancement reprend où
le run s'était arrêté : `pending_commanders()` interroge `last_scraped_at`, et
`known_deck_ids()` filtre les decks déjà présents. Rien à nettoyer entre deux runs.

`upsert_decks()` **remplace** la decklist d'un deck (delete + insert) au lieu de
la fusionner : un deck modifié sur Moxfield reflète sa nouvelle liste, pas l'union
des deux versions.

## ⚠ Alembic

`alembic/env.py` utilise `target_metadata = Base.metadata`. Les tables `mox_*`
n'y sont **pas** — un `alembic revision --autogenerate` proposera donc de les
**supprimer**. Avant le premier autogenerate, ajouter ce filtre dans `env.py` :

```python
def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table" and name.startswith("mox_"):
        return False        # tables gérées par moxfield_scraper.init_schema()
    return True
```

puis le passer à `context.configure(..., include_object=include_object)` dans les
deux fonctions (`run_migrations_offline` et `run_migrations_online`).

L'alternative, si tu veux que les `mox_*` soient versionnées par Alembic comme le
reste : rattacher les `Table(...)` de `db.py` à `Base.metadata` au lieu du
`MetaData()` local, et générer une migration.

## Architecture

```
links.py    collect_deck_ids()   -> [deck_id]        Playwright sync, pilote la liste publique
fetch.py    stream_deck_html()   -> (deck_id, html)  Playwright async, N pages en parallèle
parser.py   parse_deck_html()    -> Deck | None      fonction PURE : ni réseau, ni disque
db.py       upsert_decks()                           SQLAlchemy Core, upsert selon le dialecte
pipeline.py scrape()                                 le seul module qui fait le lien
cli.py                                               argparse ; appelé par scripts/scrape_moxfield.py
```

`parser.py` ne connaît pas la base, `db.py` ne connaît pas Moxfield. C'est ce qui
permet de tester le parser sur des fixtures HTML sans rien lancer :

```bash
pytest tests/moxfield_scraper -q
```

## Quand Moxfield change son HTML

C'est le seul point de rupture prévisible : `parser.py` dépend de classes CSS
obfusquées générées à leur build (`XIi4jFys2lGhYwseGpBo`). Le jour où
`tests/moxfield_scraper/test_parser.py` casse, tout est concentré dans ce fichier.

Filet de sécurité : lancer les gros runs avec `--html-cache data/moxfield_html`.
Les pages brutes sont conservées, et après correction du parser :

```bash
python scripts/scrape_moxfield.py reparse data/moxfield_html
```

reconstruit la base **sans re-scraper**.

## Réglages

| Option | Défaut | Effet |
|---|---|---|
| `--concurrency` | 7 | pages Chromium en parallèle — baisser si le navigateur devient instable |
| `--page-wait-ms` | 8000 | attente max du rendu de la decklist — monter si beaucoup de decks « incomplets » |
| `--no-headless` | — | affiche le navigateur, indispensable au premier run pour vérifier les sélecteurs |
| `--refresh` | — | re-télécharge même ce qui est déjà en base (rafraîchit prix, bracket, decklist) |
