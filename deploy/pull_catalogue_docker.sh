#!/usr/bin/env bash
#
# Copie le catalogue vers la base du serveur, depuis le serveur lui-meme.
#
# Sur le serveur, PostgreSQL vit dans un conteneur et n'est pas expose a
# l'exterieur ; l'hote n'a ni psql ni pg_dump. Ce script execute donc
# pull_catalogue.sh dans un conteneur postgres jetable, branche sur le reseau
# de la pile, qui apporte les deux outils et voit la base sous le nom « db ».
#
# L'URL du catalogue n'est passee qu'en argument : elle ne s'ecrit ni dans un
# fichier, ni dans le .env.
#
# Usage, depuis /app sur le serveur :
#   ./deploy/pull_catalogue_docker.sh 'postgresql://…@…render.com/relictrade?sslmode=require'
#
set -euo pipefail

CATALOGUE_URL="${1:?Usage : ./deploy/pull_catalogue_docker.sh '<URL externe Render>'}"
APP_DIR="${APP_DIR:-/app}"

cd "$APP_DIR"

if [ ! -f .env ]; then
  echo "✗ .env introuvable dans $APP_DIR." >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a; . ./.env; set +a
: "${DB_PASSWORD:?DB_PASSWORD absent du .env}"
DB_USER="${DB_USER:-manamind}"
DB_NAME="${DB_NAME:-manamind}"

# Le reseau porte le nom du projet compose, lui-meme tire du dossier passe en
# --project-directory. On le demande a Docker plutot que de le deviner.
NETWORK=$(docker network ls --filter name=_default --format '{{.Name}}' | grep -E '^(app|deploy)_default$' | head -1)
: "${NETWORK:?Reseau Docker de la pile introuvable — la pile est-elle demarree ?}"

echo "Reseau Docker   : $NETWORK"
echo "Base cible      : db:5432/$DB_NAME (dans la pile)"
echo

# postgres:18 (et non -alpine) : le script utilise bash et des tableaux, absents
# de busybox. La version majeure suit celle du serveur cible.
docker run --rm -i \
  --network "$NETWORK" \
  -v "$APP_DIR:/work" -w /work \
  -e CATALOGUE_DATABASE_URL="$CATALOGUE_URL" \
  -e TARGET_DATABASE_URL="postgresql://$DB_USER:$DB_PASSWORD@db:5432/$DB_NAME" \
  postgres:18 \
  ./deploy/pull_catalogue.sh --yes
