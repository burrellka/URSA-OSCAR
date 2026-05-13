# URSA-OSCAR — Makefile (mirrors APEX's operational patterns).
#
# Assumes bash + make available on the dev workstation (Git Bash on Windows
# is the established convention). PowerShell builds use `make build`
# which delegates to infra/build_and_push.ps1 — see Decision 13 / APEX precedent.

.PHONY: help dev up down logs build test test-backend test-mcp verify-mcp \
        verify-mcp-live import migrate backup restore clean

help: ## Show this help.
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# --- Local development ----------------------------------------------------

dev: ## Bring up the local dev stack (build + hot reload + dev-bypass port).
	docker compose -f infra/docker-compose.dev.yml up --build

up: ## Bring up the production reference stack from Docker Hub images.
	docker compose -f infra/docker-compose.yml -f infra/docker-compose.production.yml up -d

down: ## Tear down the running compose stack.
	docker compose -f infra/docker-compose.yml down

logs: ## Tail all container logs.
	docker compose -f infra/docker-compose.yml logs -f --tail=100

# --- Build + push ---------------------------------------------------------

build: ## Build + push all 4 images. Override version with VERSION=x.y.z (defaults to 0.1.0).
	powershell -ExecutionPolicy Bypass -File ./infra/build_and_push.ps1 -Version $(or $(VERSION),0.1.0)

# --- Tests ----------------------------------------------------------------

test: test-backend test-mcp ## Run all test suites.

test-backend: ## Backend unit + integration + regression tests.
	cd backend && python -m pytest -v

test-mcp: ## MCP server tool + auth boundary tests.
	cd mcp-server && python -m pytest -v

verify-mcp: ## In-process auth boundary harness (template §8 — fast, no Docker).
	cd mcp-server && python -m pytest tests/verification/test_auth_boundary.py -v

verify-mcp-live: ## Per-deploy curl verification. Usage: make verify-mcp-live HOST=http://localhost:8082
	@if [ -z "$(HOST)" ]; then echo "ERROR: HOST is required, e.g. make verify-mcp-live HOST=http://localhost:8082"; exit 1; fi
	HOST=$(HOST) bash infra/verify-mcp-live.sh

# --- Data operations ------------------------------------------------------

import: ## Import a DATALOG/SD-card dir into DuckDB. Usage: make import PATH=/path/to/source
	@if [ -z "$(PATH_ARG)" ]; then \
		echo "ERROR: PATH_ARG is required, e.g. make import PATH_ARG=backend/tests/regression/fixtures/nights/oscar-reference"; \
		exit 1; \
	fi
	cd backend && python -m ursa_oscar.ingestion.importer "$(PATH_ARG)" --verbose

migrate: ## Apply DuckDB migrations.
	cd backend && python -c "from ursa_oscar.config import get_settings; from ursa_oscar.storage.db import DuckDBManager; from ursa_oscar.storage.migrations import apply_migrations; s=get_settings(); db=DuckDBManager(s.db_path, read_only=False); v=apply_migrations(db); db.close(); print(f'Schema at v{v}')"

backup: ## Snapshot the DuckDB file to data/backups/ with a timestamp.
	@mkdir -p data/backups
	cp data/ursa-oscar.duckdb "data/backups/ursa-oscar-$$(date -u +%Y%m%dT%H%M%SZ).duckdb"
	@echo "Backup written to data/backups/"

restore: ## Restore a backup. Usage: make restore FROM=data/backups/<file>
	@if [ -z "$(FROM)" ]; then echo "ERROR: FROM is required, e.g. make restore FROM=data/backups/foo.duckdb"; exit 1; fi
	cp "$(FROM)" data/ursa-oscar.duckdb
	@echo "Restored $(FROM) to data/ursa-oscar.duckdb"

# --- Housekeeping ---------------------------------------------------------

clean: ## Remove build artefacts and pycaches.
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true
