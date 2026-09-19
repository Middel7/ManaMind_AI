#!/usr/bin/env bash
#
# Copie le CATALOGUE MAGIC depuis la base partagée (Render) vers une base cible.
#
# Le catalogue appartient au dépôt MTG-DB : ManaMind ne fait que le lire, et
# n'écrit JAMAIS dessus. Ce script ne touche donc à la source qu'en lecture.
#
# Ce qui est copié :
#   scryfall_cards, scryfall_card_faces, scryfall_card_printings,
#   scryfall_card_tags, scryfall_mtg_sets, cardmarket_products,
#   cardmarket_price_guide_entries (dernier relevé seulement, sans raw_json)
#   + la vue v_cardmarket_latest_prices_by_printing
#
# Ce qui n'est PAS copié :
#   scryfall_card_prices  — vide en production par conception (SKIP_SCRYFALL_PRICES)
#   import_runs — journal du pipeline, jamais lu ici
#   les tables RELIC — elles ne concernent pas ManaMind
#
# Usage :
#   export CATALOGUE_DATABASE_URL='postgresql://…@…render.com/relictrade?sslmode=require'
#   export TARGET_DATABASE_URL='postgresql://manamind:…@localhost:5432/manamind'
#   ./deploy/pull_catalogue.sh            # demande confirmation
#   ./deploy/pull_catalogue.sh --yes      # sans confirmation (cron)
#
set -euo pipefail

: "${CATALOGUE_DATABASE_URL:?Définissez CATALOGUE_DATABASE_URL (URL externe Render, avec sslmode=require)}"
: "${TARGET_DATABASE_URL:?Définissez TARGET_DATABASE_URL (la base qui reçoit la copie)}"

PGDUMP="${PGDUMP:-pg_dump}"
PSQL="${PSQL:-psql}"
ASSUME_YES=0
[ "${1:-}" = "--yes" ] && ASSUME_YES=1

# Tables reprises telles quelles, avec toutes leurs lignes.
FULL_TABLES=(
  scryfall_cards
  scryfall_card_faces
  scryfall_card_printings
  scryfall_card_tags
  scryfall_mtg_sets
  cardmarket_products
  # Référencée par une clé étrangère de cardmarket_price_guide_entries.
  # Elle ne pèse que quelques centaines de kilo-octets.
  cardmarket_import_files
)

# Table reprise partiellement : voir plus bas.
PRICES_TABLE=cardmarket_price_guide_entries
PRICES_VIEW=v_cardmarket_latest_prices_by_printing

# Une URL de connexion porte un mot de passe : on n'affiche que l'hôte et la base.
mask_url() {
  printf '%s' "$1" | sed -E 's#://[^@/]*@#://***@#; s#\?.*##'
}

echo "──────────────────────────────────────────────────────────────"
echo " Copie du catalogue Magic"
echo "   source : $(mask_url "$CATALOGUE_DATABASE_URL")"
echo "   cible  : $(mask_url "$TARGET_DATABASE_URL")"
echo "──────────────────────────────────────────────────────────────"

# Un dump produit par une version majeure plus récente que le serveur cible
# peut contenir des directives que ce dernier ne comprend pas.
dump_major=$("$PGDUMP" --version | grep -oE '[0-9]+' | head -1)
target_major=$("$PSQL" "$TARGET_DATABASE_URL" -tAc "SHOW server_version_num" | cut -c1-2)
if [ "$dump_major" != "$target_major" ]; then
  echo "⚠  pg_dump est en version $dump_major, le serveur cible en version $target_major."
  echo "   En cas d'erreur à la restauration, utilisez la version correspondante, par exemple :"
  echo "   PGDUMP='docker run --rm -i postgres:${target_major}-alpine pg_dump' ./deploy/pull_catalogue.sh"
fi

# Les tables du catalogue sont remplacées, pas fusionnées. Sur le poste de
# travail, la copie locale est alimentée par le pipeline MTG-DB : l'écraser avec
# celle de Render ferait perdre ce qu'elle a en plus. On demande donc confirmation
# dès qu'il y a quelque chose à remplacer.
existing=$("$PSQL" "$TARGET_DATABASE_URL" -tAc "
  SELECT COALESCE(sum(n), 0) FROM (
    SELECT (SELECT count(*) FROM scryfall_cards) AS n
    UNION ALL SELECT count(*) FROM scryfall_card_printings
  ) t" 2>/dev/null || echo 0)

if [ "${existing:-0}" -gt 0 ] && [ "$ASSUME_YES" -eq 0 ]; then
  echo
  echo "⚠  La cible contient déjà un catalogue ($existing lignes)."
  echo "   Il sera REMPLACÉ par celui de la source."
  read -r -p "Continuer ? [o/N] " answer
  case "$answer" in
    o|O|y|Y) ;;
    *) echo "Annulé."; exit 0 ;;
  esac
fi

table_args=()
for t in "${FULL_TABLES[@]}" "$PRICES_TABLE"; do
  table_args+=(--table="public.$t")
done

# ── 1. Structure ──────────────────────────────────────────────────────────────
# --clean --if-exists remplace une copie précédente sans faire échouer la
# première exécution, où rien n'existe encore côté cible.
# Les index de recherche par similarité du catalogue reposent sur pg_trgm :
# sans l'extension, la restauration du schéma échoue sur « operator class
# gin_trgm_ops does not exist ».
"$PSQL" "$TARGET_DATABASE_URL" --quiet --set ON_ERROR_STOP=on   -c "CREATE EXTENSION IF NOT EXISTS pg_trgm"

echo "→ 1/4  structure des tables et de la vue"
"$PGDUMP" "$CATALOGUE_DATABASE_URL" \
  --schema-only --no-owner --no-privileges --clean --if-exists \
  "${table_args[@]}" --table="public.$PRICES_VIEW" \
  | "$PSQL" "$TARGET_DATABASE_URL" --quiet --set ON_ERROR_STOP=on

# ── 2. Données des tables reprises intégralement ──────────────────────────────
echo "→ 2/4  données du catalogue (cartes, éditions, produits)"
full_args=()
for t in "${FULL_TABLES[@]}"; do
  full_args+=(--table="public.$t")
done
"$PGDUMP" "$CATALOGUE_DATABASE_URL" \
  --data-only --no-owner --no-privileges \
  "${full_args[@]}" \
  | "$PSQL" "$TARGET_DATABASE_URL" --quiet --set ON_ERROR_STOP=on

# ── 3. Prix : dernier relevé uniquement, sans la colonne de débogage ──────────
# La table est append-only : elle conserve un relevé par import, soit une
# cinquantaine de photos successives. ManaMind n'affiche que la cote courante
# (toutes ses requêtes se terminent par « ORDER BY captured_at DESC LIMIT 1 »),
# et raw_json ne sert qu'au diagnostic du pipeline. Ne garder que la dernière
# date sans raw_json fait passer cette table de 3,4 Go à une trentaine de Mo.
echo "→ 3/4  prix Cardmarket (dernier relevé, sans raw_json)"

# raw_json est déclarée NOT NULL à la source, où le pipeline la remplit toujours.
# La copie ne la transporte pas : on lève la contrainte sur la cible, sinon
# l'insertion échoue dès la première ligne. À refaire à chaque exécution, car
# l'étape 1 recrée la table à l'identique de la source.
"$PSQL" "$TARGET_DATABASE_URL" --quiet --set ON_ERROR_STOP=on \
  -c "ALTER TABLE $PRICES_TABLE ALTER COLUMN raw_json DROP NOT NULL"
price_columns=$("$PSQL" "$CATALOGUE_DATABASE_URL" -tAc "
  SELECT string_agg(quote_ident(column_name), ', ' ORDER BY ordinal_position)
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = '$PRICES_TABLE'
    AND column_name <> 'raw_json'")

# \copy est une méta-commande de psql : elle doit tenir sur UNE seule ligne.
# Une continuation « \ » en fin de ligne casse son analyse, et psql transmet
# alors le texte au serveur, qui répond « syntax error at or near "\" ».
copy_out=$(printf '\\copy (SELECT %s FROM %s WHERE captured_at = (SELECT MAX(captured_at) FROM %s)) TO STDOUT' \
                  "$price_columns" "$PRICES_TABLE" "$PRICES_TABLE")
copy_in=$(printf '\\copy %s (%s) FROM STDIN' "$PRICES_TABLE" "$price_columns")

"$PSQL" "$CATALOGUE_DATABASE_URL" --quiet --set ON_ERROR_STOP=on -c "$copy_out" \
  | "$PSQL" "$TARGET_DATABASE_URL" --quiet --set ON_ERROR_STOP=on -c "$copy_in"

# ── 4. Vérification ───────────────────────────────────────────────────────────
echo "→ 4/4  vérification"
# Les libellés SQL restent sans accents : sous Windows, les arguments passés à
# psql avec -c transitent par l'encodage ANSI du système, et le serveur rejette
# alors les octets reçus (« invalid byte sequence for encoding UTF8 »).
"$PSQL" "$TARGET_DATABASE_URL" -tA -F'  ' -c "
  SELECT 'cartes',        count(*)::text FROM scryfall_cards
  UNION ALL SELECT 'editions',  count(*)::text FROM scryfall_card_printings
  UNION ALL SELECT 'prix',      count(*)::text FROM $PRICES_TABLE
  UNION ALL SELECT 'vue prix',  count(*)::text FROM $PRICES_VIEW
  UNION ALL SELECT 'releve du', COALESCE(max(captured_at)::text, 'aucun') FROM $PRICES_TABLE"

echo "✓ Catalogue copié."
