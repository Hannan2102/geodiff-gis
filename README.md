# GeoDiff

GeoDiff helps a GIS analyst reconcile a field inventory of point assets with last month's
approved inventory. It finds additions, deletions, significant moves and edited attributes.
The analyst reviews the evidence, records a decision on each change, and exports an inventory
containing only the approved updates. Source files are never modified.

> Status: under construction. See [docs/PROGRESS.md](docs/PROGRESS.md).

Stack: React + TypeScript + Vite + MapLibre GL JS · FastAPI · PostgreSQL 17 / PostGIS 3.5 ·
Shapely / pyproj / GeoPandas.

## Run locally

```sh
docker compose up --build -d --wait
open http://localhost:8080
```

See [docs/SETUP.md](docs/SETUP.md) for versions, development and check commands.
