#!/usr/bin/env bash
#
# Publie en une commande ce que le poste de travail a calcule : statistiques
# (push_manamind_data.sh) et, s'ils ont change, modeles (push_models.sh).
#
# La base du serveur n'ecoute que sur 127.0.0.1 : un tunnel SSH est ouvert le
# temps de la publication, puis referme. Les identifiants de la base distante
# sont lus dans /app/.env du serveur, par la meme cle SSH : aucun mot de passe
# n'est a garder sur le poste.
#
# Usage (Git Bash, depuis la racine du depot) :
#   ./deploy/publier.sh            # demande confirmation avant d'ecrire
#   ./deploy/publier.sh --yes      # sans confirmation (lance par l'ecran admin)
#   ./deploy/publier.sh --check    # compare poste et serveur, n'ecrit rien
#
# Variables facultatives :
#   PUBLISH_HOST          serveur cible            (defaut : root@2.28.104.250)
#   SOURCE_DATABASE_URL   base du poste            (defaut : DATABASE_URL de .env)
#   PUBLISH_TUNNEL_PORT   port local du tunnel     (defaut : 15432)
#
set -euo pipefail

cd "$(dirname "$0")/.."

HOST="${PUBLISH_HOST:-root@2.28.104.250}"
PORT="${PUBLISH_TUNNEL_PORT:-15432}"
MODE="${1:-ask}"
case "$MODE" in ask|--yes|--check) ;; *)
  echo "Usage : ./deploy/publier.sh [--yes|--check]" >&2; exit 2 ;;
esac

# Les outils PostgreSQL ne sont pas dans le PATH de Git Bash sous Windows : on
# prend ceux de l'installation locale, la plus recente d'abord.
if ! command -v psql >/dev/null 2>&1; then
  for dir in /c/Program\ Files/PostgreSQL/*/bin; do
    [ -x "$dir/psql.exe" ] && PG_BIN="$dir"
  done
  if [ -z "${PG_BIN:-}" ]; then
    echo "✗ psql et pg_dump introuvables : installez PostgreSQL ou ajoutez-les au PATH." >&2
    exit 1
  fi
  export PSQL="$PG_BIN/psql.exe" PGDUMP="$PG_BIN/pg_dump.exe"
else
  export PSQL="${PSQL:-psql}" PGDUMP="${PGDUMP:-pg_dump}"
fi

# Base du poste : celle que le serveur local utilise deja.
if [ -z "${SOURCE_DATABASE_URL:-}" ]; then
  SOURCE_DATABASE_URL=$(grep -E '^DATABASE_URL=' .env | head -1 | cut -d= -f2- | tr -d '\r"')
fi
: "${SOURCE_DATABASE_URL:?DATABASE_URL introuvable dans .env}"
export SOURCE_DATABASE_URL

echo "→ lecture des identifiants de la base en ligne"
remote_env=$(ssh -o BatchMode=yes "$HOST" "grep -E '^DB_(USER|PASSWORD|NAME)=' /app/.env")
db_user=$(printf '%s\n' "$remote_env" | sed -n 's/^DB_USER=//p' | tr -d '\r"')
db_pass=$(printf '%s\n' "$remote_env" | sed -n 's/^DB_PASSWORD=//p' | tr -d '\r"')
db_name=$(printf '%s\n' "$remote_env" | sed -n 's/^DB_NAME=//p' | tr -d '\r"')
# Le mot de passe passe par PGPASSWORD plutot que par l'URL : un caractere
# special y demanderait un encodage, et l'URL s'affiche dans les journaux. La
# base source garde le sien dans sa propre URL, qui prime.
export PGPASSWORD="$db_pass"
export TARGET_DATABASE_URL="postgresql://${db_user:-manamind}@127.0.0.1:${PORT}/${db_name:-manamind}"

echo "→ ouverture du tunnel vers la base en ligne (port local $PORT)"
ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -N -L "$PORT:127.0.0.1:5432" "$HOST" &
TUNNEL_PID=$!
trap 'kill "$TUNNEL_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 20); do
  "$PSQL" "$TARGET_DATABASE_URL" -tAc "SELECT 1" >/dev/null 2>&1 && break
  sleep 1
done
if ! "$PSQL" "$TARGET_DATABASE_URL" -tAc "SELECT 1" >/dev/null 2>&1; then
  echo "✗ La base en ligne ne repond pas a travers le tunnel." >&2
  exit 1
fi

# Les modeles ne bougent qu'au reentrainement complet (Card2Vec, XGBoost,
# clustering) : on ne les renvoie que s'ils sont plus recents que la derniere
# publication, ce qui epargne 27 Mo de transfert et un redemarrage.
STAMP=data/.models_published
models_changed=0
for f in data/embeddings/card_embeddings.npy data/embeddings/card_index.json \
         data/embeddings/commander_embeddings.npy data/embeddings/commander_embeddings.json \
         data/models/xgb_card2vec.json data/clustering/cluster_annotations.json \
         data/clustering/cluster_taxonomy.json; do
  if [ -f "$f" ] && { [ ! -f "$STAMP" ] || [ "$f" -nt "$STAMP" ]; }; then
    models_changed=1
  fi
done

if [ "$MODE" = "--check" ]; then
  echo
  printf "   %-30s %12s %12s
" "table" "poste" "en ligne"
  for t in deck_stat_commander deck_stat_global deck_stat_commander_curve commanders            commander_clusters commander_cluster_meta card_neighbors card_tag_clusters            card_clusters_global tag_cluster_probabilities; do
    printf "   %-30s %12s %12s
" "$t"       "$("$PSQL" "$SOURCE_DATABASE_URL" -tAc "SELECT count(*) FROM $t" 2>/dev/null || echo '?')"       "$("$PSQL" "$TARGET_DATABASE_URL" -tAc "SELECT count(*) FROM $t" 2>/dev/null || echo '?')"
  done
  echo
  [ "$models_changed" -eq 1 ] && echo "   modeles : seraient renvoyes"                               || echo "   modeles : inchanges, ne seraient pas renvoyes"
  echo "✓ Verification terminee : rien n'a ete ecrit."
  exit 0
fi

if [ "$MODE" = "ask" ]; then
  read -r -p "Publier ces calculs sur le site en ligne ? [o/N] " answer
  case "$answer" in o|O|y|Y) ;; *) echo "Annule."; exit 0 ;; esac
fi

./deploy/push_manamind_data.sh --yes

if [ "$models_changed" -eq 1 ]; then
  ./deploy/push_models.sh "$HOST"
  touch "$STAMP"
  echo "→ redemarrage du site pour charger les nouveaux modeles"
  ssh -o BatchMode=yes "$HOST" \
    "cd /app && docker compose --project-directory /app -f deploy/docker-compose.prod.yml restart app" >/dev/null
else
  echo "→ modeles inchanges depuis la derniere publication : non renvoyes"
fi

echo "✓ Publication terminee."
