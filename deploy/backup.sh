#!/usr/bin/env bash
# Backup PostgreSQL — à lancer via cron quotidien
# crontab : 0 3 * * * /app/deploy/backup.sh >> /var/log/manamind-backup.log 2>&1

set -euo pipefail

BACKUP_DIR="/app/deploy/postgres-backup"
DATE=$(date +%Y%m%d_%H%M%S)
KEEP_DAYS=7

mkdir -p "$BACKUP_DIR"

echo "[$DATE] Début du backup..."
docker exec $(docker ps -qf "name=db") pg_dump \
    -U "${DB_USER:-manamind}" \
    "${DB_NAME:-manamind}" \
    | gzip > "$BACKUP_DIR/manamind_$DATE.sql.gz"

echo "[$DATE] Backup terminé : manamind_$DATE.sql.gz"

# Supprimer les backups de plus de 7 jours
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +$KEEP_DAYS -delete
echo "[$DATE] Anciens backups supprimés (>$KEEP_DAYS jours)"
