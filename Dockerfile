# ── Stage 1 : builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Installer uv (gestionnaire de dépendances)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copier les fichiers de dépendances
COPY pyproject.toml uv.lock* ./

# Installer les dépendances dans un répertoire virtuel
RUN uv sync --frozen --no-dev --no-install-project

# ── Stage 2 : runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Dépendances système minimales
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
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
