# Déploiement sur Hetzner

## Prérequis
- VPS Hetzner CX32 (8 Go RAM, 4 vCPU) — ~€8.5/mois
- Ubuntu 24.04 LTS
- Domaine pointant vers l'IP du VPS (A record)

## Installation initiale (une seule fois)

```bash
# 1. Installer Docker
curl -fsSL https://get.docker.com | bash
usermod -aG docker $USER

# 2. Cloner le repo
git clone https://github.com/Middel7/ManaMind_AI.git /app
cd /app

# 3. Configurer les variables d'environnement
cp .env.example .env
nano .env  # Remplir JWT_SECRET, DB_PASSWORD, CORS_ORIGINS

# 4. Construire l'image
# Elle ne contient que le serveur et le moteur d'analyse : ni Playwright,
# ni torch, ni le corpus. Compter environ 900 Mo.
docker build -t manamind:latest .

# 5. Démarrer
cd /app
docker compose --project-directory /app -f deploy/docker-compose.prod.yml up -d

# 6. Copier le catalogue Magic — voir « Remplir la base » plus bas.
#    À faire AVANT les migrations : plusieurs d'entre elles s'appuient sur le
#    catalogue, notamment la vue card_min_price, bâtie sur
#    scryfall_card_printings et cardmarket_price_guide_entries.

# 7. Créer les tables ManaMind (comptes, collections, decks)
docker compose --project-directory /app -f deploy/docker-compose.prod.yml exec app alembic upgrade head

# 8. Publier les statistiques depuis le poste de travail — voir plus bas.

# 9. Configurer le backup automatique
# IMPORTANT : rendre le script exécutable avant de l'enregistrer dans cron
chmod +x /app/deploy/backup.sh
(crontab -l 2>/dev/null; echo "0 3 * * * /app/deploy/backup.sh") | crontab -
```

> **Note :** Le script `deploy/backup.sh` est livré sans bit d'exécution (limitation de l'outil de création de fichiers).
> Exécuter `chmod +x /app/deploy/backup.sh` sur le VPS après le `git clone`.

> **`--project-directory /app` n'est pas optionnel.** Sans lui, Compose
> cherche le fichier `.env` dans `deploy/` et resout `./data` en
> `/app/deploy/data` : la base demarre sans mot de passe et les modeles
> ne sont pas trouves.

## Remplir la base

Le serveur ne scrape pas et ne calcule pas : ses données lui sont apportées par
deux scripts, depuis deux sources différentes.

L'ordre compte, car chaque étape s'appuie sur la précédente :

1. `pull_catalogue.sh` — le catalogue Magic
2. `alembic upgrade head` — les tables ManaMind, dont `card_min_price`, qui se
   construit à partir du catalogue
3. `push_manamind_data.sh` — les statistiques, depuis le poste de travail

### Le catalogue Magic — depuis la base partagée

Les cartes, éditions et prix appartiennent au dépôt MTG-DB et vivent déjà sur
Render. On en prend une copie ; on n'y écrit jamais, et la copie n'ajoute rien
au stockage facturé là-bas.

```bash
export CATALOGUE_DATABASE_URL='<External URL Render, avec ?sslmode=require>'
export TARGET_DATABASE_URL='postgresql://manamind:<mdp>@localhost:5432/manamind'
./deploy/pull_catalogue.sh
```

Environ 1,2 Go. `cardmarket_price_guide_entries` est réduite au dernier relevé
et privée de `raw_json`, ce qui la fait passer de 3,4 Go à une trentaine de Mo.
`scryfall_card_prices` n'est pas copiée : elle est vide en production, et plus
aucune requête ne la lit.

À rafraîchir une fois par semaine :

```
0 4 * * 1 cd /app && ./deploy/pull_catalogue.sh --yes >> /var/log/manamind-catalogue.log 2>&1
```

### Les statistiques ManaMind — depuis le poste de travail

À lancer **depuis le PC**, après un scraping ou un recalcul :

```bash
export SOURCE_DATABASE_URL='postgresql://manamind:<mdp>@localhost:5432/manamind'
export TARGET_DATABASE_URL='postgresql://manamind:<mdp>@<serveur>:5432/manamind'
./deploy/push_manamind_data.sh
```

Le script énumère ce qu'il va remplacer et demande confirmation. Il ne touche
jamais aux comptes ni aux collections : le serveur en est la seule autorité, et
les écraser effacerait les données des utilisateurs. `deck_cards` (12 Go de
decks bruts) ne part pas non plus — seules les statistiques qui en sont tirées
sont publiées.

## Ce qui ne fonctionne pas en production, volontairement

Le scraping Moxfield et le réentraînement s'exécutent sur le poste de travail.
Les routes `/api/admin/scrape/*` et `/api/admin/ml/retrain` répondent 503 avec
un message explicite sur le serveur : Playwright n'est pas dans l'image.

L'écran d'analyse de deck, lui, fonctionne : son moteur et ses modèles (27 Mo)
sont embarqués.

## Déploiement d'une mise à jour

```bash
cd /app
git pull
docker build -t manamind:latest .
docker compose --project-directory /app -f deploy/docker-compose.prod.yml up -d --no-deps app
```

## Rollback

```bash
# Lister les images disponibles
docker images manamind

# Revenir à une image précédente
docker tag manamind:previous manamind:latest
docker compose --project-directory /app -f deploy/docker-compose.prod.yml up -d --no-deps app
```

## Restaurer un backup

```bash
# Lister les backups
ls -la deploy/postgres-backup/

# Restaurer
gunzip -c deploy/postgres-backup/manamind_YYYYMMDD.sql.gz | \
  docker exec -i $(docker ps -qf "name=db") psql -U manamind manamind
```
