# Castle Chef developer commands. Run `make` to list them.
.DEFAULT_GOAL := help
SHELL := bash
.SHELLFLAGS := -euo pipefail -c

export NEON_PROJECT_ID := example-project
export NEON_DEV_BRANCH_ID := example-development-branch
LOCAL_DB := postgresql://postgres:postgres@localhost:54329/castle_chef
# Commands that use Neon dev read DATABASE_URL from backend/.env; one left in the shell
# (e.g. production's) must not redirect them.
unexport DATABASE_URL
# Like CI: fail rather than update a stale uv.lock.
export UV_LOCKED := 1

.PHONY: help setup deps env dev dev-reset migrate migration db db-stop test lint format check build ios

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

setup: deps ## Install dependencies and write backend/.env
	scripts/backend-env.sh

deps:
	cd backend && uv sync
	cd infra && uv sync
	cd apps/mobile && pnpm install --frozen-lockfile

env: ## Write backend/.env for Neon dev from AWS SSM
	scripts/backend-env.sh

dev: ## Run the API and the app (https://localhost:5173) against Neon dev
	scripts/dev.sh

dev-reset: ## Replace Neon dev with a copy of production, then migrate it
	@[[ -f backend/.env ]] || { echo "Missing backend/.env: run make env" >&2; exit 1; }
	@read -rp "Discard everything on Neon dev and replace it with production's data? [y/N] " answer && [[ $$answer == [yY] ]]
	neonctl branches reset $(NEON_DEV_BRANCH_ID) --parent --project-id $(NEON_PROJECT_ID)
	cd backend && uv run alembic upgrade head
	@echo "Neon dev now matches production (sign-ins too): sign in again in the app."

migrate: ## Apply migrations to Neon dev
	cd backend && uv run alembic upgrade head

migration: ## Generate a migration from models.py: make migration m="what it does"
	@test -n "$(m)" || { echo 'Usage: make migration m="what it does"' >&2; exit 2; }
	cd backend && docker compose up -d --wait
	cd backend && DATABASE_URL=$(LOCAL_DB) uv run alembic upgrade head
	cd backend && DATABASE_URL=$(LOCAL_DB) uv run alembic revision --autogenerate -m "$(m)"

db: ## Start the local Postgres used by tests and migrations
	cd backend && docker compose up -d --wait

db-stop: ## Stop the local Postgres (its data is kept)
	cd backend && docker compose stop

test: db ## Run all tests: backend, app, infra
	cd backend && TEST_DATABASE_URL=$(LOCAL_DB) uv run pytest
	cd apps/mobile && pnpm test
	cd infra && uv run pytest

lint: ## Lint and check formatting
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd infra && uv run ruff check . && uv run ruff format --check .
	cd apps/mobile && pnpm lint

format: ## Format Python code
	cd backend && uv run ruff format .
	cd infra && uv run ruff format .

check: deps lint test build ## What CI checks

build: ## Build the app for production (talks to production)
	cd apps/mobile && pnpm build

ios: build ## Copy the build into the iOS project and open Xcode (Mac)
	cd apps/mobile && pnpm exec cap sync ios && pnpm exec cap open ios
