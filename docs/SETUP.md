# Setup and commands

## Prerequisites (versions verified on 2026-11-18)

| Tool | Version used | Notes |
| --- | --- | --- |
| Docker Engine / Compose | 29.7.2 | Docker Desktop on macOS arm64 |
| Python | 3.12.14 | managed by uv; `requires-python >=3.12,<3.13` |
| uv | 0.12.7 | Python lockfile: `api/uv.lock` |
| Node.js | 22.13.1 | npm 10.9.2; lockfile: `web/package-lock.json` |
| PostgreSQL / PostGIS | 17.6 / 3.5.3 | image `imresamu/postgis:17-3.5` locally and in CI |

`imresamu/postgis` is a multi-arch build of the official `postgis/postgis` Dockerfile; the
official image only publishes amd64, which runs under emulation on Apple silicon.

Pinned application dependencies: FastAPI 0.165.0, uvicorn 0.54.0, psycopg 3.3.9,
psycopg-pool 3.3.3, Shapely 2.2.0, pyproj 3.8.0, GeoPandas 1.2.0 (analysis extra),
React 19.3.1, Vite 8.3.8, TypeScript 5.9.3, MapLibre GL JS 6.19.0, Playwright 1.65.1.

> Keep the checkout outside iCloud/Dropbox-synced folders (for example `~/Developer`).
> File-sync daemons evict `.venv`/`node_modules` files and make imports block.

## One-command local start

```sh
docker compose up --build -d --wait   # or: make up
open http://localhost:8080
```

This starts PostGIS (persistent volume `geodiff_pgdata`), runs the `migrate` job, starts the
API on `localhost:8000`, and serves the built web app on `localhost:8080`.
Database and API ports bind to 127.0.0.1 only.

## Hot-reload development

```sh
make dev-db     # PostGIS only
make dev-api    # applies migrations, then uvicorn --reload on :8000
make dev-web    # Vite on :5173
```

## Checks

```sh
make check      # ruff, ruff format, mypy --strict, pytest (with PostGIS), eslint, tsc, vitest, build
make e2e        # Playwright against the composed stack
```

Tests marked `db` run only when `DATABASE_URL` is set; CI always sets it.

## Endpoints

- `GET /health`: liveness; does not touch the database.
- `GET /ready`: database reachability, Postgres/PostGIS versions, applied and pending
  migrations. Returns 503 until all migrations are applied.
- `GET /docs`, `GET /openapi.json`: OpenAPI contract.

## Migrations

`python -m geodiff.migrate` applies `api/migrations/NNNN_*.sql` in order. It holds a Postgres
advisory lock, so concurrent release jobs serialize. Each file runs in its own transaction and
records a checksum, and editing an already-applied file is refused.
