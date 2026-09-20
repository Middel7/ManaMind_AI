# ManaMind AI — instructions de travail

Application web de collection et de decks Magic (format Commander), en français,
à usage privé. Python 3.12 + FastAPI, PostgreSQL via SQLAlchemy/Alembic, et un
front sans framework : des HTML statiques posés sur un socle maison.

Référence détaillée : [docs/PROJECT_DOCUMENTATION.md](docs/PROJECT_DOCUMENTATION.md).
Ce fichier-ci ne la résume pas — il fixe les règles à tenir en écrivant du code.

---

## Lancer et tester

```powershell
.venv\Scripts\python.exe start.py        # serveur sur 127.0.0.1:8080
.venv\Scripts\python.exe -m pytest       # 100 tests, ~2 s
```

- **Jamais `uv run`** : numba 0.53.1 (via umap-learn) casse la résolution en 3.12.
- **Jamais `pip install`** : `uv add` / `uv remove` / `uv sync`, et `pyproject.toml`
  et `uv.lock` se committent ensemble.
- **`127.0.0.1`, pas `localhost`** : sur cette machine, `localhost` coûte ~2 s de
  pénalité de résolution IPv6 par connexion. Toute mesure de performance faite
  sur `localhost` est fausse.

---

## Langue

Le projet distingue trois registres. Cette règle est tenue sur 3 500 lignes de
front et 25 000 lignes de Python : la rompre laisse une trace visible.

| Où | Règle |
|---|---|
| Texte vu par l'utilisateur (HTML, chaînes JS, messages d'API) | Français **pleinement accentué** |
| Commentaires CSS et JS | Français **sans accents** — « degrade », « superieure », « lateral » |
| Commentaires et docstrings Python | Français **accentué** |
| Messages de commit | Français **sans accents** |

Vérification : `app.css` et `tokens.css` ne contiennent aucun caractère accentué ;
`mm.js` en contient 43, tous dans des chaînes affichées.

**« EDHREC » ne paraît jamais dans l'interface** ni dans aucun texte lu par
l'utilisateur. Dire « popularité dans les decks », « fréquence d'inclusion », ou
un équivalent neutre.

---

## Interface

### Le socle

Trois fichiers portent tout le front. Les lire avant d'écrire une ligne d'UI.

| Fichier | Contenu |
|---|---|
| `static/css/tokens.css` | 117 lignes de jetons : couleurs, typo, espacements, rayons, durées |
| `static/css/app.css` | 1 648 lignes de composants, ~200 classes |
| `static/js/mm.js` | 1 772 lignes : client API, session, composants, icônes |

Une page se réduit à un `<div id="page">` et un appel à `MM.boot()`, qui
construit la coquille autour (barre latérale sur desktop, barre d'onglets en bas
sur mobile) :

```js
const user = await MM.boot({ title: 'Mes decks', nav: 'decks' });
if (!user) return;
```

Les fonctions les plus employées : `MM.el` / `MM.els`, `MM.esc` (**toujours**
échapper ce qui vient de l'API avant de l'insérer en HTML), `MM.api.get/post/patch/del`,
`MM.fmt.int/eur/pct/dec/plural`, `MM.cardTile`, `MM.cardDetail`, `MM.resolveCards`,
`MM.empty`, `MM.toast.ok/error`, `MM.modal`, `MM.confirm`, `MM.cardSkeletons`,
`MM.autocomplete`, `MM.deckPicker`, `MM.toolIntro`, `MM.icons.*`.

### Direction artistique

Interface sombre où **l'illustration des cartes porte la couleur ; le chrome
reste neutre**. Accent or `--accent` (#E0A94A), titres en Marcellus, données en
Inter, chiffres en `font-variant-numeric: tabular-nums`. Les visuels d'ambiance
prennent le cadrage `art_crop` de Scryfall via `MM.scryfallArt(id)`.

Toute carte respecte `--card-ratio` (63 × 88 mm) et le rayon `4.6% / 3.3%` de
`.mtg-card__frame`. Une vignette de carte n'est jamais réécrite à la main :
`MM.cardTile(item, options)`.

### Interdits

- **Pas de valeur en dur** là où un jeton existe : ni `#E0A94A`, ni `16px`, ni
  `0.22s`, ni `rgba(255,255,255,.06)`. Utiliser `--accent`, `--sp-4`, `--dur`,
  `--surface`. Une valeur nue ne se justifie que pour une dimension propre à un
  composant (`width: 26px` sur un bouton de stepper), et alors elle est commentée.
- **Pas de composant nouveau avant d'avoir cherché l'existant.** `app.css` couvre
  déjà : `.btn` (+ `--primary --ghost --outline --danger --lg --sm --icon --block`),
  `.panel`, `.stat`, `.badge`, `.chip`, `.row`, `.card-grid`, `.mtg-card`,
  `.modal`, `.toast`, `.empty`, `.skeleton`, `.spinner`, `.progress`, `.step`,
  `.curve`, `.mana-*`, `.pip`, `.toolbar`, `.intro`, `.walk`, `.checks`, `.legend`,
  `.deck-strip`, plus les utilitaires `.stack`, `.cluster`, `.between`, `.grow`,
  `.grid-2`, `.grid-3`, `.truncate`, `.sr-only`.
- **Pas de framework, pas de bundler, pas de `package.json`.** Node.js et Prisma
  ont été écartés explicitement.
- **Pas d'emoji** dans l'interface. Les pictogrammes viennent de `MM.icons`, en
  SVG `stroke="currentColor"` sur une grille 24.
- **Pas de `innerHTML` sans `MM.esc`** sur des données venues de l'API.
- **Pas de thème clair** : `color-scheme: dark` est assumé, il n'y a pas de
  variante à maintenir.

### CSS local à une page

Un bloc `<style>` en tête de page est légitime pour un ajustement qui ne
concerne qu'elle. Trois conditions :

1. il est préfixé par une classe propre à la page (`.hero-dash .hero__title`) ;
2. il **ne redéfinit pas** un composant partagé sans le préfixe — modifier `.btn`
   ou `.panel` depuis une page est interdit ; ce qui doit changer partout change
   dans `app.css` ;
3. chaque règle porte un commentaire disant **pourquoi**, pas ce qu'elle fait.
   C'est la voix du dépôt : « les 300 px imposes laissaient un grand vide sous
   les chiffres », pas « reduit la hauteur ».

### Responsive

Points de rupture en place : 1400, 1080, **860** (bascule sidebar → tiroir +
barre d'onglets), 720, 680, 420. S'y tenir plutôt qu'en inventer. Vérifier
qu'aucune grille ne déborde horizontalement : les grilles portent `min-width: 0`
sur leurs enfants pour cette raison.

---

## Backend

- Point d'entrée `server.py` à la racine (pas un package) ; les routes vivent
  dans `src/manamind/routers/` (12 routers, 121 endpoints).
- Les pages HTML sont servies par `routers/pages.py`, en `FileResponse` avec
  `Cache-Control: no-cache, no-store, must-revalidate`.
- Les modèles ORM ne sont pas ici : ils viennent du paquet externe `mtgdb`
  (dépôt `Middel7/MTG-DB`), partagé avec un autre projet. `src/manamind/db/`
  n'en est qu'un ré-export, qui recrée l'engine avec son pool.
- Les réponses JSON passent par `_json_response` (UTF-8 non échappé).
- `torch`, `pandas`, `scikit-learn` n'ont rien à faire dans un import de module
  servi en production : ces groupes restent sur le poste de travail, sauf
  `analyze` dont dépend le moteur de deck.

---

## Base de données

- La base de production `relictrade` (Render) est **partagée avec le projet
  RELIC**. ManaMind la réplique, il ne la possède pas.
- Les tables `scryfall_*`, `cardmarket_*` et `user_deck_cards` appartiennent au
  rôle `postgres` : **`CREATE INDEX` y est impossible** depuis nos migrations.
  Une migration Alembic qui les touche échouera en ligne.
- Les jointures sur les noms de cartes passent par `mm_normalize_name()`, avec
  repli sur la face avant. Appeler cette fonction **des deux côtés** d'une
  jointure produit une boucle imbriquée quadratique : pré-agréger les noms
  normalisés dans un CTE, puis laisser le planificateur faire un hash join.
- `scryfall_card_prices` est vide en production.

---

## Commits

Format : `type(portee): effet observable`, en français **sans accents**, décrivant
ce que l'utilisateur constate — pas ce que le code fait.

```
feat(decks): le menu de choix d'un deck se lit de A a Z
fix(prix): les cotes du changement de commandant survivent a la mise en ligne
fix(alembic): les migrations ne touchent plus au catalogue partage
```

**Pousser systématiquement après un commit**, sans redemander.

---

## Avant de rendre une page

1. Aucune couleur, espacement ou durée en dur qu'un jeton couvrait.
2. Aucun composant réécrit alors que `app.css` en avait un.
3. Les données de l'API passent par `MM.esc`.
4. Les états vides (`MM.empty`) et de chargement (`MM.cardSkeletons`) existent.
5. Le texte visible est accentué ; les commentaires CSS/JS ne le sont pas.
6. « EDHREC » n'apparaît nulle part dans le rendu.
7. La page tient à 860 px et à 420 px sans débordement horizontal.
8. Les tests passent : `.venv\Scripts\python.exe -m pytest`.
