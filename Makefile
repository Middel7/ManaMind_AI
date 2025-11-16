# Makefile pour ManaMind AI
export PYTHONPATH := .
# Système de recommandation MTG Commander

.PHONY: help install sync clean lint format check test run pre-commit lock update

# Couleurs pour l'affichage
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

##@ Aide

help: ## Afficher cette aide
	@echo "$(BLUE)ManaMind AI - Commandes disponibles:$(NC)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf ""} /^[a-zA-Z_-]+:.*?##/ { printf "  $(GREEN)%-15s$(NC) %s\n", $$1, $$2 } /^##@/ { printf "\n$(YELLOW)%s$(NC)\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Installation et Configuration

install: ## Installer l'environnement et les dépendances
	@echo "$(BLUE)Installation de l'environnement...$(NC)"
	uv sync
	uv run pre-commit install
	@echo "$(GREEN)✓ Installation terminée$(NC)"

sync: ## Synchroniser les dépendances
	@echo "$(BLUE)Synchronisation des dépendances...$(NC)"
	uv sync
	@echo "$(GREEN)✓ Synchronisation terminée$(NC)"

lock: ## Mettre à jour uv.lock
	@echo "$(BLUE)Mise à jour de uv.lock...$(NC)"
	uv lock
	@echo "$(GREEN)✓ Lock file mis à jour$(NC)"

update: ## Mettre à jour toutes les dépendances
	@echo "$(BLUE)Mise à jour des dépendances...$(NC)"
	uv lock --upgrade
	uv sync
	@echo "$(GREEN)✓ Dépendances mises à jour$(NC)"

##@ Code Quality

lint: ## Linter le code avec Ruff (avec auto-fix)
	@echo "$(BLUE)Linting du code avec Ruff...$(NC)"
	uv run ruff check --fix src/ tests/
	@echo "$(GREEN)✓ Linting terminé$(NC)"

format: ## Formater le code avec Ruff
	@echo "$(BLUE)Formatage du code avec Ruff...$(NC)"
	uv run ruff format src/ tests/
	@echo "$(GREEN)✓ Formatage terminé$(NC)"

check: ## Vérifier le code sans modification (lint + format check)
	@echo "$(BLUE)Vérification du code...$(NC)"
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/
	@echo "$(GREEN)✓ Vérification terminée$(NC)"

fix: lint format ## Corriger et formater tout le code

##@ Tests

test: ## Exécuter les tests
	@echo "$(BLUE)Exécution des tests...$(NC)"
	uv run pytest tests/ -v
	@echo "$(GREEN)✓ Tests terminés$(NC)"

test-cov: ## Exécuter les tests avec couverture
	@echo "$(BLUE)Exécution des tests avec couverture...$(NC)"
	uv run pytest tests/ -v --cov=src/manamind --cov-report=html --cov-report=term
	@echo "$(GREEN)✓ Tests terminés - Rapport dans htmlcov/index.html$(NC)"

test-watch: ## Exécuter les tests en mode watch
	@echo "$(BLUE)Tests en mode watch...$(NC)"
	uv run pytest-watch tests/

##@ Pre-commit

pre-commit: ## Exécuter pre-commit sur tous les fichiers
	@echo "$(BLUE)Exécution de pre-commit...$(NC)"
	uv run pre-commit run --all-files
	@echo "$(GREEN)✓ Pre-commit terminé$(NC)"

pre-commit-update: ## Mettre à jour les hooks pre-commit
	@echo "$(BLUE)Mise à jour des hooks pre-commit...$(NC)"
	uv run pre-commit autoupdate
	@echo "$(GREEN)✓ Hooks mis à jour$(NC)"

##@ Application

run: ## Lancer l'application principale
	@echo "$(BLUE)Démarrage de ManaMind AI...$(NC)"
	uv run main.py

ingest: ## Ingérer les données des decks
	@echo "$(BLUE)Ingestion des données...$(NC)"
	uv run -m manamind.ingestor


##@ Nettoyage

clean: ## Nettoyer les fichiers temporaires
	@echo "$(BLUE)Nettoyage des fichiers temporaires...$(NC)"
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	rm -rf htmlcov/ 2>/dev/null || true
	rm -rf dist/ 2>/dev/null || true
	rm -rf build/ 2>/dev/null || true
	@echo "$(GREEN)✓ Nettoyage terminé$(NC)"

clean-all: clean ## Nettoyer tout (incluant .venv)
	@echo "$(BLUE)Nettoyage complet...$(NC)"
	rm -rf .venv/
	@echo "$(GREEN)✓ Nettoyage complet terminé$(NC)"

