#!/usr/bin/env bash
#
# Publie les CALCULS MANAMIND depuis le poste de travail vers le serveur.
#
# Ce script ne transporte que ce que l'atelier fabrique : statistiques par
# commandant, regroupements de cartes, référentiel des commandants. Il ne
# touche JAMAIS aux comptes ni aux collections : celles-ci naissent sur le
# serveur, qui en est la seule autorité. Les écraser reviendrait à effacer
# les données des utilisateurs.
#
# Usage :
#   export SOURCE_DATABASE_URL='postgresql://manamind:…@localhost:5432/manamind'
#   export TARGET_DATABASE_URL='postgresql://manamind:…@mon-serveur:5432/manamind'
#   ./deploy/push_manamind_data.sh            # demande confirmation
#   ./deploy/push_manamind_data.sh --yes      # sans confirmation (cron)
#
set -euo pipefail

: "${SOURCE_DATABASE_URL:?Définissez SOURCE_DATABASE_URL (la base du poste de travail)}"
: "${TARGET_DATABASE_URL:?Définissez TARGET_DATABASE_URL (la base du serveur)}"

PGDUMP="${PGDUMP:-pg_dump}"
PSQL="${PSQL:-psql}"
ASSUME_YES=0
[ "${1:-}" = "--yes" ] && ASSUME_YES=1

# Résultats des scripts hors ligne : le poste de travail en est l'autorité,
# le serveur n'en est que le lecteur. Ils sont remplacés à chaque publication.
PUBLISHED_TABLES=(
  deck_stat_commander
  deck_stat_global
  deck_stat_commander_curve
  commanders
  commander_clusters
  commander_cluster_meta
  card_neighbors
  card_tag_clusters
  card_clusters_global
  tag_cluster_probabilities
)

# Vue matérialisée : on recopie sa définition, puis le serveur la recalcule
# lui-même. Inutile de transporter des lignes qu'une requête reconstruit.
MATERIALIZED_VIEW=card_min_price

# Jamais publiées. La liste est vérifiée à l'exécution : si l'une d'elles
# se glissait dans PUBLISHED_TABLES, le script s'arrête avant d'écrire.
declare -a PROTECTED_TABLES=(
  users user_profiles user_collection user_deck_cards
  user_preferred_printings user_hidden_moves user_opened_sets
  user_moxfield_decks invitations
)

for published in "${PUBLISHED_TABLES[@]}"; do
  for protected in "${PROTECTED_TABLES[@]}"; do
    if [ "$published" = "$protected" ]; then
      echo "✗ ABANDON : « $published » contient des données utilisateur et ne doit jamais être publiée." >&2
      exit 1
    fi
  done
done

# Une URL de connexion porte un mot de passe : on n'affiche que l'hôte et la base.
mask_url() {
  printf '%s' "$1" | sed -E 's#://[^@/]*@#://***@#; s#\?.*##'
}

# Publier avant qu'Alembic n'ait créé le schéma laisse le serveur dans un état
# impossible à rattraper : les tables publiées existent déjà, et un
# « alembic upgrade head » ultérieur échoue sur un CREATE TABLE en doublon.
# L'ordre d'installation est : pull_catalogue.sh, puis alembic, puis ce script.
schema_ready=$("$PSQL" "$TARGET_DATABASE_URL" -tAc \
  "SELECT to_regclass('public.alembic_version') IS NOT NULL")
if [ "$schema_ready" != "t" ]; then
  echo "✗ ABANDON : le schéma ManaMind n'existe pas encore sur la cible." >&2
  echo "  Lancez d'abord « alembic upgrade head » sur le serveur." >&2
  exit 1
fi

echo "──────────────────────────────────────────────────────────────"
echo " Publication des calculs ManaMind"
echo "   source : $(mask_url "$SOURCE_DATABASE_URL")"
echo "   cible  : $(mask_url "$TARGET_DATABASE_URL")"
echo "──────────────────────────────────────────────────────────────"
echo
echo " Tables remplacées sur le serveur :"
for t in "${PUBLISHED_TABLES[@]}"; do
  n=$("$PSQL" "$SOURCE_DATABASE_URL" -tAc "SELECT count(*) FROM $t" 2>/dev/null || echo "?")
  printf "   %-32s %12s lignes\n" "$t" "$n"
done
echo
echo " Tables laissées intactes (données des utilisateurs) :"
printf "   %s\n" "${PROTECTED_TABLES[*]}"
echo
echo " Non transportée : deck_cards (les decks bruts restent sur le poste)."
echo

if [ "$ASSUME_YES" -eq 0 ]; then
  read -r -p "Publier ? [o/N] " answer
  case "$answer" in
    o|O|y|Y) ;;
    *) echo "Annulé."; exit 0 ;;
  esac
fi

table_args=()
for t in "${PUBLISHED_TABLES[@]}"; do
  table_args+=(--table="public.$t")
done

# --clean --if-exists remplace la version précédente ; l'ensemble passe dans une
# seule transaction (--single-transaction), si bien qu'un transfert interrompu
# laisse le serveur sur ses anciennes données plutôt que sur une copie tronquée.
echo "→ transfert en cours…"
"$PGDUMP" "$SOURCE_DATABASE_URL" \
  --no-owner --no-privileges --clean --if-exists \
  "${table_args[@]}" --table="public.$MATERIALIZED_VIEW" \
  | "$PSQL" "$TARGET_DATABASE_URL" --quiet --single-transaction --set ON_ERROR_STOP=on

echo "→ recalcul de la vue matérialisée"
"$PSQL" "$TARGET_DATABASE_URL" --quiet --set ON_ERROR_STOP=on \
  -c "REFRESH MATERIALIZED VIEW $MATERIALIZED_VIEW"

echo "→ vérification"

# Les tables de comptes sont créées sur le serveur par « alembic upgrade head » :
# lors d'une toute première publication, elles peuvent manquer encore. Un CASE
# ne suffit pas à s'en prémunir — PostgreSQL analyse la requête entière avant
# de l'exécuter, et échoue sur la branche morte. D'où ce test préalable.
count_rows() {
  local table="$1"
  local exists
  exists=$("$PSQL" "$TARGET_DATABASE_URL" -tAc "SELECT to_regclass('public.$table') IS NOT NULL")
  if [ "$exists" = "t" ]; then
    "$PSQL" "$TARGET_DATABASE_URL" -tAc "SELECT count(*) FROM $table"
  else
    echo "table absente (lancez alembic upgrade head)"
  fi
}

printf "   %-30s %s\n" "statistiques par commandant" "$(count_rows deck_stat_commander)"
printf "   %-30s %s\n" "statistiques globales"       "$(count_rows deck_stat_global)"
printf "   %-30s %s\n" "commandants"                 "$(count_rows commanders)"
printf "   %-30s %s\n" "comptes (intacts)"           "$(count_rows users)"
printf "   %-30s %s\n" "collection (intacte)"        "$(count_rows user_collection)"

echo "✓ Calculs publiés."
