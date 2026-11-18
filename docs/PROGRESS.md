# Progress

Legend: **implemented** (code exists) · **tested** (automated check passes) · **pushed**
(on GitHub) · **publicly verified** (checked on the deployed site).

## Phase 1: Runnable foundation (implemented, tested)

Completed behavior
- Repository layout: `api/` (FastAPI, migrations, tests), `web/` (React/Vite/TS), `fixtures/`,
  `scripts/`, `docs/`, `.github/workflows/`, `compose.yaml`.
- Compose stack with separate backend and frontend networks and a persistent `pgdata` volume:
  `db` → `migrate` (one-shot release job) → `api` → `web` (nginx serving the built app).
- Advisory-locked, checksummed, forward-only SQL migration runner with bounded connect retry.
- `/health` (liveness) and `/ready` (PostGIS version and migration state; 503 when not ready),
  request IDs, aggregate request logs without bodies, machine-readable error envelope,
  CORS restricted to configured origins.
- Readiness screen in React that reports ready, degraded (API up, DB down) or unreachable,
  with retry.
- CI: ruff, ruff format, mypy strict, pytest against a PostGIS service container; eslint, tsc,
  vitest, Vite build; clean compose boot plus a Playwright connectivity check.

Commands and results (local, macOS arm64, 2026-11-18)
- `make check-api`: ruff/format/mypy clean; pytest 3 passed (includes PostGIS readiness and
  migration idempotency).
- `make check-web`: eslint, tsc clean; vitest 2 passed; build OK.
- `docker compose up --build -d --wait`: exit 0; `/ready` reports PostgreSQL 17.6, PostGIS 3.5.3,
  migrations `[0001_postgis]`.
- `npx playwright test`: 1 passed (browser → API → PostGIS).
- CORS: `Origin: http://localhost:8080` allowed; an unknown origin receives no
  `Access-Control-Allow-Origin`.

Limitations
- No domain schema or comparison engine yet.
- Fonts are system fallbacks. Bundled fonts are decided in Phase 4.

Next: Phase 2, reproducible fixtures and the comparison engine.
