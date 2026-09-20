# Prompt de challenge qualité — ManaMind AI

> À copier-coller tel quel dans ChatGPT. Le but n'est pas d'obtenir un résumé
> du projet, mais une contradiction argumentée sur sa qualité technique.
> État des lieux relevé le 2026-09-19 sur la branche `feat/prod-security-perf`.

---

## Rôle que tu dois tenir

Tu es un architecte logiciel senior chargé d'un audit contradictoire. Le projet
ci-dessous est décrit par l'équipe qui l'a écrit : considère cette description
comme le point de vue de l'accusé, pas comme la vérité. Ton travail est de
produire les questions et les objections qui feraient mal en revue
d'architecture, puis de les hiérarchiser.

Tu n'as pas accès au dépôt. Tu ne peux donc pas vérifier : formule tes attaques
sous forme d'hypothèses de défaillance falsifiables, chacune assortie du test ou
de la commande qui permettrait de la confirmer ou de l'infirmer.

---

## 1. Ce que fait le produit

Application web de gestion de collection et de decks Magic: The Gathering, format
Commander/EDH, à usage privé (inscription sur invitation, quelques utilisateurs).

Fonctions :
- gérer une collection physique, exemplaire par exemplaire (édition, numéro de
  collecteur, finition, langue, état, rangement) ;
- importer une collection ou un deck (fichier ou texte, plusieurs formats) ;
- suivre ses decks, leur taux de possession, leur coût de complétion ;
- recommander des cartes à ajouter ou à retirer d'un deck, à partir de
  statistiques d'inclusion calculées sur ~36 000 decklists publiques scrapées,
  puis d'un moteur d'analyse (embeddings + clustering + modèle appris) ;
- proposer des commandants alternatifs pour un deck donné, classés par valeur
  de cartes conservée ;
- un écran d'administration (scraping, statistiques, comptes).

Équipe : un développeur, assisté d'agents LLM. 164 commits, 3 auteurs dont un
seul significatif.

---

## 2. Chiffres du dépôt (mesurés, pas estimés)

| Mesure | Valeur |
|---|---|
| Fichiers Python | 121 |
| Lignes Python (`src/` + `scripts/` + `tests/`) | 25 311 |
| Endpoints HTTP (décorateurs `@router.*`) | 117, répartis sur 12 routers |
| Pages HTML servies | 22 fichiers, 8 331 lignes |
| JS applicatif partagé | `static/js/mm.js`, 1 772 lignes |
| CSS | `app.css` 1 648 lignes + `tokens.css` 117 lignes |
| Migrations Alembic dans ce dépôt | 26 |
| Tests | 100, tous verts, 1,86 s d'exécution |
| Violations `ruff check` | 896 (dont 394 `E501`, 120 `ANN201`, 107 `I001`, 34 `E402`, 23 `F401`) |
| Intégration continue | aucune (pas de `.github/`) |
| Hook pre-commit | déclaré en dépendance dev, aucun `.pre-commit-config.yaml` |
| Mesure de couverture | aucune |
| Poids du `.git` | 42 Mo |

Les plus gros fichiers applicatifs : `scripts/deck_improver.py` (1 526 l.),
`src/manamind/collection_advisor.py` (1 273 l.),
`src/manamind/routers/collection.py` (1 170 l.),
`src/manamind/collection_store.py` (849 l.),
`src/manamind/routers/collection_v2.py` (801 l.),
`src/manamind/routers/scrape.py` (754 l.), `admin.html` (1 474 l.).

---

## 3. Stack

- Python 3.12.8 (version exacte imposée par `requires-python = "==3.12.8"`).
- FastAPI + Uvicorn, point d'entrée `server.py` à la racine (pas un package).
- `uv` comme gestionnaire de dépendances, `uv.lock` commité. `pip` proscrit.
- PostgreSQL, SQLAlchemy 2.0, Alembic.
- Les modèles ORM vivent dans un package externe `mtgdb`, installé depuis un
  dépôt GitHub (`mtgdb @ git+https://github.com/Middel7/MTG-DB.git`), partagé
  avec un autre projet. `src/manamind/db/` n'est qu'un ré-export, qui recrée
  toutefois l'engine avec un pool explicite (`pool_size=5`, `max_overflow=10`,
  `pool_recycle=1800`, `pool_pre_ping=True`).
- Auth : `python-jose` (JWT) + `bcrypt`, `slowapi` pour le rate limiting,
  `sentry-sdk` optionnel.
- Groupes de dépendances : le serveur n'embarque que FastAPI + DB + auth ;
  `analyze` (numpy/pandas/scikit-learn/xgboost) est installé en production car
  le moteur d'analyse en dépend ; `ml` (torch, sentence-transformers, gensim,
  shap, umap, hdbscan) et `scrape` (Playwright) restent sur le poste de travail.
- Front : aucun framework, aucun bundler, aucun `package.json`. HTML statiques +
  un socle maison `MM.boot()` dans `mm.js`. Node.js et Prisma ont été refusés
  explicitement.
- Lint : `ruff` configuré (line-length 100, règles `E,W,F,I,B,UP,ANN`), mais
  jamais appliqué de bout en bout (896 violations restantes).

---

## 4. Architecture serveur

`server.py` (≈430 lignes) assemble : logging, Sentry conditionnel, CORS,
middleware d'en-têtes de sécurité, 12 routers, la route racine, `/health`,
`/api/version`, les montages statiques, et une route attrape-tout
`GET /{filename:path}` déclarée en dernier.

Routers : `admin_users`, `auth`, `collection`, `collection_v2`,
`commander_swap`, `dashboard`, `decks`, `decks_v2`, `engine`, `import_deck`,
`pages`, `scrape`.

Points d'architecture assumés par l'équipe :

- **Deux générations d'API cohabitent.** `collection.py` (20 endpoints,
  API historique) et `collection_v2.py` (17 endpoints) ; `decks.py` (11) et
  `decks_v2.py` (10). Aucune date de retrait de la v1 n'est fixée. Un endpoint
  `/api/v2/commander-build/{commander}` vit d'ailleurs dans le fichier v1.
- **Le cycle de vie applique les migrations au démarrage** : `alembic upgrade
  head` en sous-processus, timeout 120 s, échec = refus de démarrer.
- **Le moteur d'analyse est un singleton chargé dynamiquement** :
  `importlib.util.spec_from_file_location` sur `scripts/deck_improver.py`, soit
  un script de recherche importé comme module de production, protégé par un
  verrou global, préchargé en tâche de fond au démarrage.
- **Un seul worker Uvicorn**, justifié par l'empreinte mémoire du moteur
  (~2 Go). Les endpoints sont majoritairement synchrones, donc exécutés dans le
  pool de threads de Starlette.
- **Le scraping Moxfield tourne dans le processus du serveur**, en threads
  lancés par les routes `/api/admin/scrape/*`, avec un suivi de jobs en mémoire
  et des métadonnées persistées dans deux fichiers JSON à la racine
  (`ml_metadata.json`, `scrape_metadata.json`).
- **SQL : 203 appels à `text()`** contre une poignée de requêtes via l'ORM. Les
  14 requêtes construites en f-string assemblent des fragments statiques
  (clauses `WHERE`/`SET` conditionnelles) et lient toujours les valeurs par
  paramètres nommés.
- Les pages HTML sont servies **sans contrôle d'authentification côté serveur** :
  `pages.py` renvoie le fichier, et c'est le JavaScript qui redirige vers
  `/login` quand une API répond 401.

---

## 5. Front-end

Pas de build. Chaque page est un HTML qui charge `tokens.css`, `app.css`,
`mm.js`, déclare un `<div id="page">` et appelle `MM.boot({...})`. `mm.js`
expose un client API, la session, la navigation, les composants (modale, toast,
autocomplétion, vignette de carte, sélecteur de deck) et le formatage.

Le reste de la logique de chaque écran vit dans un `<script>` inline en fin de
page — de 130 à 638 lignes selon l'écran, 1 474 pour `admin.html`.

Les statiques sont servies avec `Cache-Control: no-cache` (revalidation forcée)
pour éviter qu'un `mm.js` périmé casse une page à jour.

---

## 6. Données et base

- La base de production est **partagée avec un autre projet** hébergé sur
  Render. ManaMind ne possède pas les tables `scryfall_*` ni `cardmarket_*` :
  elles appartiennent à un autre rôle PostgreSQL. Conséquence directe :
  **impossible de créer un index** sur ces tables depuis ce projet.
- Les jointures de noms passent par une fonction SQL maison
  `mm_normalize_name()` (NFD, suppression des accents) qui reproduit
  `scryfall_cards.normalized_name`, avec repli sur la face avant des cartes
  recto-verso. Appeler cette fonction des deux côtés d'une jointure produit une
  boucle imbriquée quadratique ; le contournement retenu est de pré-agréger les
  noms dans une CTE pour retrouver un hash join.
- Tables propres au projet : `users`, `invitations`, `user_profiles`,
  `user_collection`, `user_deck_cards`, `user_preferred_printings`,
  `deck_stat_global`, `deck_stat_commander`, `deck_stat_commander_curve`,
  tables de clustering et de voisinage de cartes.
- `deck_stat_commander_curve` est **calculée paresseusement** : un commandant
  absent est calculé à la première demande (~0,1 s) puis enregistré.
- Volumes : ~30 000 cartes Scryfall, 25 130 lignes de statistiques globales,
  120 799 couples (commandant, carte).

---

## 7. Chaîne data / ML

Hors ligne, sur le poste de travail :
1. scraping Moxfield (Playwright) → decklists en base ;
2. `mox_to_stats.py` → `deck_stat_commander` + courbes de mana ;
3. `compute_commander_tfidf.py` → profils TF-IDF par commandant ;
4. `build_card2vec.py` → Word2Vec skip-gram, 128 dimensions, sur les decklists
   (un deck = une phrase, une carte = un mot), plus des vecteurs de commandants
   pondérés TF-IDF ;
5. `build_ml_dataset.py` + `evaluate_models.py` → XGBoost, comparaison avec et
   sans similarité cosinus Card2Vec, métriques RMSE/MAE/R², P@K, NDCG@K, SHAP ;
6. clustering d'archétypes (HDBSCAN/UMAP), annotation des clusters.

En production, seul `deck_improver.py` tourne : il recharge les embeddings, les
clusters, les profils TF-IDF et un modèle appris pour produire un profil de
deck, une analyse d'écart au méta du commandant, un top 30 d'ajouts et de
retraits, des paires de remplacement et des explications.

Les artefacts (embeddings `.npy`, modèles, CSV de stats) sont livrés **dans
l'image Docker** et non montés en volume. Aucun versionnement de modèle, aucun
registre, aucune procédure de rollback d'un modèle ; la traçabilité tient dans
un `ml_metadata.json` à la racine.

---

## 8. Sécurité

Ce qui est en place :
- JWT signé HS256 dans un cookie `httponly`, `samesite=lax`, `secure`
  conditionné par `HTTPS_ENABLED`, durée 7 jours ;
- mots de passe hachés bcrypt ;
- avertissement au démarrage si `JWT_SECRET` n'est pas défini (mais le serveur
  démarre quand même avec une clé par défaut connue) ;
- inscription par invitation à usage unique, consommée atomiquement
  (`UPDATE ... WHERE used_at IS NULL RETURNING id`) ;
- rôle `admin` exigé sur les 12 routes `/api/admin/*` et les routes de comptes,
  avec garde-fou « ne jamais supprimer le dernier administrateur actif » ;
- middleware d'en-têtes : `X-Content-Type-Options`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, `X-XSS-Protection`, HSTS si HTTPS ;
- CORS restreint par variable d'environnement, `allow_credentials=True` ;
- route attrape-tout avec liste blanche d'extensions, blocage des noms
  sensibles (`.env`, `pyproject.toml`, `alembic.ini`, `uv.lock`, tout fichier
  commençant par un point) et vérification anti-traversée de chemin ;
- corps de requête illisible traité en 400 plutôt qu'en 500 ;
- 12 tests de sécurité couvrant ces protections.

Ce qui ne l'est pas, ou pas clairement :
- **le rate limiting ne couvre que 6 endpoints sur 117** (login, inscription,
  `/auth/me`, deux routes du moteur, une route de commandants alternatifs) ;
  rien sur les routes d'administration, de scraping ni sur les écritures de
  collection ;
- **`/data`, `/uploads` et `/outputs` sont montés en `StaticFiles` sans
  authentification** ; `/data` contient les embeddings, les modèles et les
  decklists, `/uploads` les fichiers déposés par les utilisateurs ;
- aucune protection CSRF explicite au-delà de `SameSite=Lax` ;
- aucune politique de sécurité de contenu (CSP), alors que chaque page exécute
  du JavaScript inline ;
- pas de révocation de jeton ni de rotation de secret ;
- pas d'analyse de dépendances (`pip-audit`, Dependabot) ni de scan de secrets.

---

## 9. Déploiement et exploitation

- Dockerfile multi-étages : build `uv sync --frozen --no-default-groups --group
  analyze`, runtime `python:3.12-slim` avec `libpq5`, utilisateur non-root,
  healthcheck sur `/health`, `CMD python server.py`.
- `docker-compose.prod.yml` : PostgreSQL 18-alpine, l'application liée à
  `127.0.0.1:8080`, Caddy en frontal (TLS automatique, HTTP/3, HSTS, logs
  tournants). Volumes `uploads` et `outputs` persistés, `data` volontairement
  non monté pour ne pas masquer les modèles livrés dans l'image.
- Scripts de déploiement : `backup.sh`, `pull_catalogue.sh`,
  `push_manamind_data.sh`, un README Hetzner.
- `/health` teste la connexion base et l'état du moteur, renvoie 503 en mode
  dégradé. `/api/version` expose le nombre de commits, le SHA et la date, lus
  une fois par `git` au démarrage (donc `git` doit être présent dans l'image).
- Supervision : Sentry si `SENTRY_DSN` est défini, échantillonnage des traces à
  10 %. Aucune métrique applicative, aucun tableau de bord, aucune alerte autre
  que Sentry.
- Le déploiement est manuel : pas de pipeline, pas de tests joués avant mise en
  ligne, pas de stratégie de retour arrière documentée autre que redéployer
  l'image précédente.

---

## 10. État réel de la qualité

**Tests.** 100 tests, 1,86 s, tous verts. Leur portée : `conftest.py` force
`DATABASE_URL=sqlite:///:memory:`, neutralise le cycle de vie (ni Alembic ni
moteur IA) et s'appuie sur le fait que « la plupart des routes renvoient 401/403
avant toute requête SQL ». Répartition : 49 tests sur les parseurs d'import,
15 sur la normalisation des commandants, 13 sur l'authentification, 12 sur la
sécurité HTTP, 11 sur le scraper. **Aucun test ne touche `collection_advisor`,
`collection_store`, `commander_swap`, `deck_improver` ni la moindre requête SQL
réelle** — c'est-à-dire ni le cœur métier, ni les 203 requêtes `text()`.

**Documentation.** `docs/PROJECT_DOCUMENTATION.md` (882 lignes) se présente comme
la source unique de vérité, mais est partiellement périmée : elle affirme que
« les migrations sont gérées exclusivement dans MTG-DB » alors que ce dépôt en
contient 26 ; elle décrit des pages et des scripts qui n'existent plus
(`recommendations_view_slide16.html`, `compute_deck_stats.py`,
`deck_config.html`) ; son en-tête annonce une dernière mise à jour antérieure à
la moitié des sections qu'elle contient.

**Dette identifiée mais non planifiée.** 14 marqueurs `TODO`/`FIXME`, dont cinq
« supprimer après migration » qui désignent des chemins de repli sur des
fichiers locaux (`My decks/*.txt`, `My_commanders.txt`) coexistant avec la base.
Deux générations d'API en parallèle. Un script de recherche importé comme module
de production.

**Conventions.** Ruff est configuré avec des règles exigeantes (type hints
obligatoires, imports triés) et n'est manifestement pas exécuté : 896
violations, dont des imports inutilisés et des variables mortes. Rien
n'empêche un commit de dégrader l'état : ni hook, ni CI.

---

## 11. Contraintes et arbitrages à ne pas remettre en cause

Ces choix sont tranchés ; ne perds pas de temps à les contester, attaque plutôt
leurs conséquences.

1. Python + FastAPI + PostgreSQL. Node.js et Prisma ont été refusés
   explicitement.
2. `uv` uniquement, jamais `pip`.
3. Pas de framework front, pas de bundler, pas de `package.json`.
4. Les modèles ORM restent dans le package externe partagé `mtgdb`.
5. La base de production est partagée et ses tables de catalogue ne sont pas
   modifiables par ce projet.
6. Le projet est à usage privé, quelques utilisateurs, hébergé sur un VPS
   unique. Une réponse du type « il faut du Kubernetes » est hors sujet.

---

## 12. Ce que j'attends de toi

Produis, dans cet ordre :

1. **Les dix questions les plus dérangeantes** que tu poserais à l'auteur, celles
   dont la réponse révélerait un problème réel. Pas de généralités : chaque
   question doit viser un élément précis de la description ci-dessus.
2. **Les hypothèses de défaillance**, classées par gravité × probabilité. Pour
   chacune : le scénario concret (quelles données, quelle séquence, quel effet
   observable), et le test qui la confirme ou l'infirme. Traite en particulier,
   sans t'y limiter :
   - l'interaction entre un seul worker, les endpoints synchrones, le pool de
     threads de Starlette et un pool de connexions de 5 (+10) ;
   - le scraping et le réentraînement lancés dans le processus qui sert les
     requêtes ;
   - les migrations appliquées au démarrage sur une base partagée avec un autre
     projet ;
   - le calcul paresseux de `deck_stat_commander_curve` sous requêtes
     concurrentes ;
   - l'exposition de `/data`, `/uploads` et `/outputs` sans authentification ;
   - les pages protégées uniquement par le JavaScript ;
   - la cohabitation durable des API v1 et v2 ;
   - la valeur réelle d'une suite de 100 tests qui n'exécute aucun SQL.
3. **Le plan de remédiation minimal** : ce que tu ferais dans les deux premiers
   jours, en visant le meilleur rapport risque évité / effort, pour un
   développeur seul. Dis explicitement ce que tu ne ferais **pas**, et pourquoi.
4. **Le point aveugle** : une chose que cette description ne mentionne pas et
   dont l'absence même t'inquiète.

Sois direct et spécifique. Si un point de la description te paraît suspect ou
trop beau, dis-le et explique ce que tu vérifierais.
