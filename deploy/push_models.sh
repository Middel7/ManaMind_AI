#!/usr/bin/env bash
#
# Depose sur le serveur les modeles dont l'ecran d'analyse de deck a besoin.
#
# Ces fichiers sont produits par l'entrainement, sur le poste de travail. Ils
# sont dans .gitignore : ils ne voyagent pas par le depot, et l'image Docker ne
# les contient donc pas. Ce script les publie, comme push_manamind_data.sh
# publie les statistiques.
#
# A relancer apres chaque reentrainement des modeles.
#
# Usage :
#   ./deploy/push_models.sh root@2.28.104.250
#   ./deploy/push_models.sh root@mon-domaine.com /app
#
set -euo pipefail

TARGET="${1:?Usage : ./deploy/push_models.sh <utilisateur@serveur> [chemin distant]}"
REMOTE_DIR="${2:-/app}"

# Les sept fichiers que scripts/deck_improver.py ouvre reellement.
# card2vec.model (34 Mo) et xgb_baseline.json ne servent qu'a l'entrainement :
# ils restent sur le poste de travail.
FILES=(
  data/embeddings/card_embeddings.npy
  data/embeddings/card_index.json
  data/embeddings/commander_embeddings.npy
  data/embeddings/commander_embeddings.json
  data/models/xgb_card2vec.json
  data/clustering/cluster_annotations.json
  data/clustering/cluster_taxonomy.json
)

echo "──────────────────────────────────────────────────────────────"
echo " Publication des modeles d'analyse"
echo "   vers : $TARGET:$REMOTE_DIR/data"
echo "──────────────────────────────────────────────────────────────"

missing=0
for f in "${FILES[@]}"; do
  if [ -f "$f" ]; then
    printf "   %-46s %8s\n" "$f" "$(du -h "$f" | cut -f1)"
  else
    echo "   MANQUANT : $f" >&2
    missing=1
  fi
done

if [ "$missing" -eq 1 ]; then
  echo >&2
  echo "Des modeles manquent sur ce poste. Relancez l'entrainement avant de publier." >&2
  exit 1
fi

# Les dossiers doivent exister cote serveur avant la copie : scp ne les cree pas.
ssh "$TARGET" "mkdir -p $REMOTE_DIR/data/embeddings $REMOTE_DIR/data/models $REMOTE_DIR/data/clustering"

echo
echo "→ transfert en cours…"
for f in "${FILES[@]}"; do
  scp -q "$f" "$TARGET:$REMOTE_DIR/$f"
done

echo "→ verification"
ssh "$TARGET" "du -sh $REMOTE_DIR/data; find $REMOTE_DIR/data -type f | sort | sed 's|^|   |'"

echo "✓ Modeles publies. Redemarrez l'application pour qu'elle les recharge :"
echo "  ssh $TARGET 'cd $REMOTE_DIR && docker compose -f deploy/docker-compose.prod.yml restart app'"
