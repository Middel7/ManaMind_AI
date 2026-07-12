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
docker build -t manamind:latest .

# 5. Démarrer
cd /app
docker compose -f deploy/docker-compose.prod.yml up -d

# 6. Configurer le backup automatique
# IMPORTANT : rendre le script exécutable avant de l'enregistrer dans cron
chmod +x /app/deploy/backup.sh
(crontab -l 2>/dev/null; echo "0 3 * * * /app/deploy/backup.sh") | crontab -
```

> **Note :** Le script `deploy/backup.sh` est livré sans bit d'exécution (limitation de l'outil de création de fichiers).
> Exécuter `chmod +x /app/deploy/backup.sh` sur le VPS après le `git clone`.

## Déploiement d'une mise à jour

```bash
cd /app
git pull
docker build -t manamind:latest .
docker compose -f deploy/docker-compose.prod.yml up -d --no-deps app
```

## Rollback

```bash
# Lister les images disponibles
docker images manamind

# Revenir à une image précédente
docker tag manamind:previous manamind:latest
docker compose -f deploy/docker-compose.prod.yml up -d --no-deps app
```

## Restaurer un backup

```bash
# Lister les backups
ls -la deploy/postgres-backup/

# Restaurer
gunzip -c deploy/postgres-backup/manamind_YYYYMMDD.sql.gz | \
  docker exec -i $(docker ps -qf "name=db") psql -U manamind manamind
```
