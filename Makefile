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

.PHONY: help setup deps env dev review dev-reset migrate migration db db-stop worker-image instacart-login worker-login worker-job test lint format check build ios

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

setup: deps ## Install dependencies and write backend/.env
	scripts/backend-env.sh

deps:
	cd backend && uv sync
	# The browser the checkout tools' tests drive (the build uv.lock's Playwright expects).
	cd backend && uv run playwright install --only-shell chromium
	cd infra && uv sync
	cd apps/mobile && pnpm install --frozen-lockfile

env: ## Write backend/.env for Neon dev from AWS SSM
	scripts/backend-env.sh

dev: ## Run the API and the app (https://localhost:5173) against Neon dev
	scripts/dev.sh

review: ## Review the app's UI from a phone on the local network, with comments (Neon dev)
	scripts/review.sh

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

worker-image: ## Build the worker image (Fargate's) from the working tree
	scripts/worker-docker.sh build

instacart-login: ## Sign Castle Chef in to Instacart's website for checkout (a browser opens here; saved to SSM)
	cd backend && AWS_PROFILE=conn AWS_REGION=us-east-1 \
	  CASTLE_CHEF_INSTACART_SESSION_PARAMETER=/castle-chef/shared/instacart-session \
	  uv run castle-chef instacart sign-in

worker-login: ## Connect the local worker image to Instacart through Codex (once)
	scripts/worker-docker.sh login codex

worker-job: ## Run one pending job in the worker image, as Fargate does: make worker-job JOB=<id>
	@test -n "$(JOB)" || { echo 'Usage: make worker-job JOB=<job-id>' >&2; exit 2; }
	scripts/worker-docker.sh run $(JOB)

test: db ## Run all tests: backend, app, infra
	cd backend && TEST_DATABASE_URL=$(LOCAL_DB) uv run pytest
	cd apps/mobile && pnpm test
	cd infra && uv run pytest

lint: ## Lint, check formatting, and type-check
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd infra && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd apps/mobile && pnpm lint

format: ## Format Python code
	cd backend && uv run ruff format .
	cd infra && uv run ruff format .

check: deps lint test build ## What CI checks

build: ## Build the app for production (talks to production)
	cd apps/mobile && pnpm build

ios: build ## Copy the build into the iOS project and open Xcode (Mac)
	cd apps/mobile && pnpm exec cap sync ios && pnpm exec cap open ios
