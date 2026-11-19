# GeoDiff developer commands. See docs/SETUP.md.
.PHONY: up down logs migrate dev-db dev-api dev-web check check-api check-web test e2e fixtures sample

LOCAL_DB := postgresql://geodiff:geodiff-local-dev@localhost:5433/geodiff
UV := env -u VIRTUAL_ENV uv

up:            ## Build and start db, migrations, API and web (http://localhost:8080)
	docker compose up --build -d --wait

down:          ## Stop the stack (the database volume is kept)
	docker compose down

logs:
	docker compose logs -f --tail=100

migrate:       ## Apply migrations to the local compose database
	cd api && DATABASE_URL=$(LOCAL_DB) $(UV) run python -m geodiff.migrate

dev-db:        ## Start only PostGIS for hot-reload development
	docker compose up -d --wait db

dev-api: migrate
	cd api && DATABASE_URL=$(LOCAL_DB) $(UV) run uvicorn geodiff.main:app --reload --port 8000

dev-web:
	cd web && npm run dev

check: check-api check-web   ## Lint, type-check and unit/integration tests

check-api:
	cd api && $(UV) run ruff check . && $(UV) run ruff format --check . && $(UV) run mypy
	cd api && DATABASE_URL=$(LOCAL_DB) $(UV) run pytest -q

check-web:
	cd web && npm run lint && npm run typecheck && npm test && npm run build

e2e: up        ## Browser tests against the composed stack
	cd web && npx playwright test

fixtures:      ## Regenerate synthetic fixtures, context layers and invalid examples
	cd api && $(UV) run geodiff generate --out ../fixtures
	python3 scripts/make_invalid_fixtures.py

sample:        ## Rebuild the guided-sample bundle and parity fixtures from committed inputs
	cd api && $(UV) run geodiff sample
