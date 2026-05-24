# ============================================
# LabLink AI - Makefile
# ============================================
# Common commands for development and deployment
#
# Usage:
#   make help        - Show available commands
#   make up          - Start development environment
#   make down        - Stop all services
#   make logs        - View logs
#   make test        - Run tests
#   make migrate     - Run database migrations

.PHONY: help up down build rebuild logs logs-api logs-db test shell migrate migrate-new clean reset

# Default target
.DEFAULT_GOAL := help

# Colors for output
CYAN := \033[0;36m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

# ============================================
# Help
# ============================================

help: ## Show this help message
	@echo ""
	@echo "$(CYAN)LabLink AI - Development Commands$(NC)"
	@echo ""
	@echo "$(GREEN)Usage:$(NC) make [target]"
	@echo ""
	@echo "$(YELLOW)Targets:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-15s$(NC) %s\n", $$1, $$2}'
	@echo ""

# ============================================
# Docker Compose - Development
# ============================================

up: ## Start development environment
	@echo "$(GREEN)Starting LabLink AI (development)...$(NC)"
	docker compose up -d
	@echo ""
	@echo "$(GREEN)Services started:$(NC)"
	@echo "  API:      http://localhost:8000"
	@echo "  API Docs: http://localhost:8000/docs"
	@echo "  MinIO:    http://localhost:9001 (admin/minioadmin)"
	@echo "  Postgres: localhost:5432"
	@echo ""
	@echo "$(YELLOW)Run 'make logs' to view logs$(NC)"

up-build: ## Start with rebuild
	@echo "$(GREEN)Building and starting LabLink AI...$(NC)"
	docker compose up -d --build

down: ## Stop all services
	@echo "$(YELLOW)Stopping LabLink AI...$(NC)"
	docker compose down

stop: down ## Alias for down

build: ## Build Docker images
	@echo "$(GREEN)Building Docker images...$(NC)"
	docker compose build

rebuild: ## Force rebuild Docker images (no cache)
	@echo "$(GREEN)Rebuilding Docker images (no cache)...$(NC)"
	docker compose build --no-cache

restart: ## Restart all services
	@echo "$(YELLOW)Restarting LabLink AI...$(NC)"
	docker compose restart

restart-api: ## Restart only API service
	@echo "$(YELLOW)Restarting API service...$(NC)"
	docker compose restart api

# ============================================
# Docker Compose - Production
# ============================================

up-prod: ## Start production environment
	@echo "$(GREEN)Starting LabLink AI (production)...$(NC)"
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
	@echo ""
	@echo "$(GREEN)Production services started$(NC)"

down-prod: ## Stop production environment
	@echo "$(YELLOW)Stopping LabLink AI (production)...$(NC)"
	docker compose -f docker-compose.yml -f docker-compose.prod.yml down

build-prod: ## Build production Docker images
	@echo "$(GREEN)Building production Docker images...$(NC)"
	docker compose -f docker-compose.yml -f docker-compose.prod.yml build

# ============================================
# Logs
# ============================================

logs: ## View all service logs (follow)
	docker compose logs -f

logs-api: ## View API logs (follow)
	docker compose logs -f api

logs-db: ## View PostgreSQL logs (follow)
	docker compose logs -f postgres

logs-minio: ## View MinIO logs (follow)
	docker compose logs -f minio

# ============================================
# Shell Access
# ============================================

shell: ## Open shell in API container
	@echo "$(GREEN)Opening shell in API container...$(NC)"
	docker compose exec api /bin/bash

shell-db: ## Open psql shell in PostgreSQL
	@echo "$(GREEN)Opening psql shell...$(NC)"
	docker compose exec postgres psql -U postgres -d lablink

shell-minio: ## Open shell in MinIO container
	docker compose exec minio /bin/sh

# ============================================
# Database Migrations
# ============================================

migrate: ## Run database migrations
	@echo "$(GREEN)Running database migrations...$(NC)"
	docker compose exec api alembic upgrade head

migrate-repair: ## Stamp existing init_db schema at 001, then apply 002 (fixes DuplicateTable)
	@echo "$(YELLOW)Repairing Alembic history for existing database...$(NC)"
	@echo "$(YELLOW)Marking 001_initial as applied (tables already exist from init_db)...$(NC)"
	docker compose exec api alembic stamp 001_initial
	@echo "$(GREEN)Applying bioprocess migration 002...$(NC)"
	docker compose exec api alembic upgrade head
	@echo "$(GREEN)Done. Current revision:$(NC)"
	docker compose exec api alembic current

migrate-down: ## Rollback last migration
	@echo "$(YELLOW)Rolling back last migration...$(NC)"
	docker compose exec api alembic downgrade -1

migrate-new: ## Create new migration (usage: make migrate-new MSG="description")
	@if [ -z "$(MSG)" ]; then \
		echo "$(RED)Error: MSG is required$(NC)"; \
		echo "Usage: make migrate-new MSG=\"your migration description\""; \
		exit 1; \
	fi
	@echo "$(GREEN)Creating new migration: $(MSG)$(NC)"
	docker compose exec api alembic revision --autogenerate -m "$(MSG)"

migrate-history: ## Show migration history
	docker compose exec api alembic history

migrate-current: ## Show current migration version
	docker compose exec api alembic current

migrate-sql: ## Generate SQL for pending migrations
	docker compose exec api alembic upgrade head --sql

# ============================================
# Testing
# ============================================

test: ## Run tests
	@echo "$(GREEN)Running tests...$(NC)"
	docker compose exec api pytest -v

test-cov: ## Run tests with coverage
	@echo "$(GREEN)Running tests with coverage...$(NC)"
	docker compose exec api pytest -v --cov=. --cov-report=term-missing

test-watch: ## Run tests in watch mode
	@echo "$(GREEN)Running tests in watch mode...$(NC)"
	docker compose exec api pytest-watch

compchem-ui-install: ## Install comp-chem React dashboard dependencies
	cd frontend/compchem-dashboard && npm install

compchem-ui-dev: ## Start comp-chem React dashboard on http://localhost:5173
	cd frontend/compchem-dashboard && npm run dev

compchem-ui-build: ## Build comp-chem React dashboard
	cd frontend/compchem-dashboard && npm run build

wetlab-ui-install: ## Install wet-lab React dashboard dependencies
	cd frontend/wetlab-dashboard && npm install

wetlab-ui-dev: ## Start wet-lab React dashboard on http://localhost:5174
	cd frontend/wetlab-dashboard && npm run dev

wetlab-ui-build: ## Build wet-lab React dashboard
	cd frontend/wetlab-dashboard && npm run build

wetlab-seed: ## Seed demo wet lab campaign (requires Postgres on localhost:5432)
	POSTGRES_HOST=localhost python scripts/seed_demo_wetlab.py

demo-qa-install: ## Install Playwright dependencies for demo screenshot QA
	cd frontend/demo-qa && npm install && npx playwright install --with-deps chromium

demo-screenshots: ## Run Playwright tour QA against the running stack ($DEMO_BASE_URL or http://localhost:3000)
	@echo "$(CYAN)Running comp-chem and wet-lab tour screenshot specs...$(NC)"
	cd frontend/demo-qa && npx playwright test

demo-share-report: ## Print tracked share-link opens (DATABASE_URL must point at the demo DB)
	python scripts/demo_share_report.py

lint: ## Run linting
	@echo "$(GREEN)Running linter...$(NC)"
	docker compose exec api ruff check .

format: ## Format code
	@echo "$(GREEN)Formatting code...$(NC)"
	docker compose exec api ruff format .

# ============================================
# Development Utilities
# ============================================

status: ## Show status of all services
	@echo "$(CYAN)Service Status:$(NC)"
	docker compose ps

health: ## Check health of services
	@echo "$(CYAN)Health Check:$(NC)"
	@curl -s http://localhost:8000/api/v1/health | python3 -m json.tool || echo "$(RED)API not responding$(NC)"

ps: status ## Alias for status

top: ## Show running processes
	docker compose top

stats: ## Show container resource usage
	docker stats --no-stream $$(docker compose ps -q)

# ============================================
# Cleanup
# ============================================

clean: ## Remove stopped containers and dangling images
	@echo "$(YELLOW)Cleaning up...$(NC)"
	docker compose down --remove-orphans
	docker image prune -f

clean-volumes: ## Remove all volumes (WARNING: deletes data!)
	@echo "$(RED)WARNING: This will delete all data!$(NC)"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	docker compose down -v
	docker volume rm lablink-pgdata lablink-minio-data 2>/dev/null || true

reset: clean-volumes up ## Reset everything and start fresh (WARNING: deletes data!)

# ============================================
# Setup
# ============================================

init: ## Initial setup (copy .env, build, start)
	@echo "$(GREEN)Initializing LabLink AI...$(NC)"
	@if [ ! -f .env ]; then \
		echo "$(YELLOW)Creating .env from .env.example...$(NC)"; \
		cp .env.example .env; \
	fi
	@echo "$(GREEN)Building and starting services...$(NC)"
	$(MAKE) up-build
	@echo ""
	@echo "$(GREEN)Waiting for services to be healthy...$(NC)"
	@sleep 10
	@echo ""
	@echo "$(GREEN)Running migrations...$(NC)"
	$(MAKE) migrate
	@echo ""
	@echo "$(GREEN)LabLink AI is ready!$(NC)"
	@echo "  API:      http://localhost:8000"
	@echo "  API Docs: http://localhost:8000/docs"

# ============================================
# Edge Agent
# ============================================

edge-start: ## Start edge agent (host mode)
	@echo "$(GREEN)Starting edge agent...$(NC)"
	@mkdir -p sample_data/incoming
	cd edge && python agent.py --watch ../sample_data/incoming --api http://localhost:8000 --org demo-lab

edge-test: ## Drop test file for edge agent
	@echo "$(GREEN)Creating test file...$(NC)"
	@mkdir -p sample_data/incoming
	echo -e "time,temperature,yield\n0,37.1,0.80\n1,38.5,0.76\n2,36.9,0.85" > sample_data/incoming/test_$$(date +%s).csv
	@echo "$(GREEN)Test file created in sample_data/incoming/$(NC)"

# ============================================
# Documentation
# ============================================

docs: ## Open API documentation in browser
	@echo "$(GREEN)Opening API documentation...$(NC)"
	@open http://localhost:8000/docs 2>/dev/null || xdg-open http://localhost:8000/docs 2>/dev/null || echo "Open http://localhost:8000/docs in your browser"
