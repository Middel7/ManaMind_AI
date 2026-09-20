# ── Stage 1 : builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# mtgdb s'installe depuis un dépôt Git : sans le client git, uv s'arrête sur
# « Git executable not found ». L'image python:slim ne l'embarque pas, et cette
# couche ne concerne que l'étape de construction — le runtime n'en hérite pas.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Installer uv (gestionnaire de dépendances)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# uv télécharge volontiers son propre CPython. Le venv pointerait alors vers un
# interpréteur qui n'existe pas dans l'étage final, et le serveur démarrerait
# sur « ModuleNotFoundError: No module named 'dotenv' » — le venv étant bien là,
# mais inutilisable. On impose l'interpréteur de l'image.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3

# Copier les fichiers de dépendances
COPY pyproject.toml uv.lock* ./

# Installer UNIQUEMENT les dépendances du serveur.
# --no-default-groups écarte dev, ml et scrape : ni torch, ni scikit-learn,
# ni Playwright dans l'image. Ces groupes ne servent qu'au poste de travail.
RUN uv sync --frozen --no-install-project --no-default-groups --group analyze

# ── Stage 2 : runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Dépendances système minimales.
#
# pg_dump et psql ne sont pas un confort : le bouton « Tirer le catalogue » de
# l'écran d'administration exécute deploy/pull_catalogue.sh depuis ce conteneur.
# Il ne peut pas passer par pull_catalogue_docker.sh, qui lance un conteneur
# jetable — cela demanderait de monter le socket Docker ici, c'est-à-dire de
# donner à l'application un accès root à la machine hôte.
#
# La version 18 vient du dépôt PGDG, et non de Debian qui en est restée à la 15 :
# pg_dump refuse de lire une base plus récente que lui, et la pile tourne sur
# postgres:18-alpine. Le paquet ajoute une vingtaine de mégaoctets à l'image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    bash \
    ca-certificates \
    curl \
    gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-18 \
    && apt-get purge -y gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copier le venv depuis le builder
COPY --from=builder /app/.venv /app/.venv

# Variables d'environnement
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app/src"

# Créer un utilisateur non-root
RUN groupadd -r manamind && useradd -r -g manamind -d /app -s /sbin/nologin manamind

# Copier le code source
COPY --chown=manamind:manamind . .

# Créer les répertoires nécessaires
RUN mkdir -p uploads outputs/recommendations data && \
    chown -R manamind:manamind uploads outputs data

USER manamind

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

EXPOSE 8080

CMD ["python", "server.py"]
