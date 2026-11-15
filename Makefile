.DEFAULT_GOAL := help
SHELL := /bin/bash

DATA_DIR ?= data/generated
CUSTOMERS ?= 40000
SEED ?= 42

.PHONY: help init install up down lint fmt typecheck test test-int test-all data seed migrate \
	profile dev stop smoke stack-up stack-down

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

init: ## Create .env with generated local secrets (never overwrites)
	@uv run python scripts/init_env.py

install: ## Install locked dependencies
	uv sync --locked

up: ## Start PostgreSQL and wait until it is healthy
	docker compose up -d --wait postgres

down: ## Stop containers (data volume is kept)
	docker compose down

lint: ## Ruff lint + format check
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts

fmt: ## Apply formatting and safe lint fixes
	uv run ruff check --fix src tests scripts
	uv run ruff format src tests scripts

typecheck: ## mypy (strict)
	uv run mypy

test: ## Unit tests (no external services)
	uv run pytest -m "not integration and not e2e"

test-int: ## Integration tests (needs `make up`)
	uv run pytest -m integration

test-all: ## Everything with coverage (needs `make up`)
	uv run pytest --cov --cov-report=term-missing

data: ## Generate the synthetic data pack (CUSTOMERS=40000 for dev; 400000 for evaluation and load)
	uv run skylink-datagen generate --out $(DATA_DIR) --customers $(CUSTOMERS) --seed $(SEED)

migrate: ## Apply database migrations
	uv run --env-file .env care-admin migrate

seed: migrate ## Re-seed the mock gateway and start the care platform from a clean state
	uv run --env-file .env mock-oss seed --data $(DATA_DIR)
	uv run --env-file .env care-admin reset-runtime
	uv run --env-file .env care-admin load-reference --data $(DATA_DIR)
	uv run --env-file .env care-admin index-guides

profile: ## Write the mapping-quality profile to reports/data_profile.md
	uv run --env-file .env care-admin profile

dev: up seed ## Start PostgreSQL, seed (clean state), then run the five processes on this machine
	./scripts/dev_stack.sh start

stop: ## Stop the local processes
	./scripts/dev_stack.sh stop

smoke: ## Live journey check against the running local stack
	uv run --env-file .env python scripts/smoke.py

stack-up: ## Everything in containers (generates and seeds data on first run)
	docker compose --profile stack up -d --build --wait

stack-down: ## Stop the container stack (volumes are kept)
	docker compose --profile stack down
