# Benchmarks and accuracy

All numbers below were measured on 2026-11-18 at commit `0726a10` plus this phase's
benchmark script. Raw results with full environment records are in
[benchmarks/engine.json](benchmarks/engine.json), [benchmarks/api.json](benchmarks/api.json)
and [benchmarks/cold.json](benchmarks/cold.json). They are measurements from one laptop, not
guarantees. The free-tier deployment is slower; its own numbers are recorded in the deployment
section once it exists.

## Reproduce

```sh
docker compose up --build -d --wait          # API part and cold part need the stack
cd api
uv run python ../scripts/benchmark.py engine  # in-process only, no stack needed
uv run python ../scripts/benchmark.py api     # against http://localhost:8000
uv run python ../scripts/benchmark.py cold    # restarts the api container 3 times
```

## Environment

| | |
|---|---|
| Hardware | Apple M1 (8 cores), 8 GB RAM, macOS 26.5.2 arm64 |
| Docker | 29.7.2, VM with 8 CPUs and 3.8 GiB; API, PostGIS and web in containers |
| Engine | Python 3.12.14, pyproj 3.8.0 (PROJ 9.8.1), shapely 2.2.0, numpy 2.5.5 |
| Database | PostgreSQL 17.6, PostGIS 3.5.3 (`imresamu/postgis:17-3.5`) |
| Inputs | `geodiff.synth.generate`, seed `20261118`, tolerance 2 m, 7 compared fields |

## Method

- Timer: `time.perf_counter` around each call; each figure gives the median with min and max.
- **Engine** (computation only: no HTTP, no database, no serialization of the response):
  7 runs per size. Parse is both files from bytes; compare is `compare_datasets`; resolve is a
  draft export with no decisions (every component left at baseline).
- **API** (client-observed wall time over loopback HTTP; includes JSON encode/decode, the
  PostGIS writes and reads, and Docker networking): 5 runs per size, each in a **fresh
  workspace** that is deleted afterwards, so nothing is cached between runs. Uploads are raw
  GeoJSON bodies. The queue is paged 200 assets at a time.
- **Network floor**: 50 `GET /health` round trips give the transport cost on its own (median
  2.2 ms). The figures below are not adjusted for it.
- **Cold start**: `docker compose restart api` until `/ready` reports ready (container stop,
  start, interpreter import, database pool and migration check). This is *not* the free
  host's wake-from-sleep time; that is measured after deployment.
- The 1,000 and 10,000 inputs scale the 5,000-asset change mix proportionally, keeping 10
  near-threshold moves on each side of the tolerance. The API benchmark stops at 5,000
  because uploads are capped at 10,000 features and the 10k proposed file has 10,020.

## Detection accuracy (labeled synthetic fixture)

The generator writes an independent manifest of every change it injects, so each detected
component can be checked by ID and kind. Scope: synthetic point data only, with the
generator's change types (additions, deletions, moves, attribute edits, ID changes, type
drift, null/absent changes, GPS jitter, importer timestamps). These figures do **not**
describe accuracy on real city data, where the ground truth is unknown.

| Assets | Labeled changes (denominator) | TP | FP | FN | Precision | Recall |
|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 119 (15 add, 13 del, 35 move, 56 attr) | 119 | 0 | 0 | 1.000 | 1.000 |
| 5,000 | 543 (75 add, 65 del, 131 move, 272 attr) | 543 | 0 | 0 | 1.000 | 1.000 |
| 10,000 | 1,074 (150 add, 130 del, 251 move, 543 attr) | 1,074 | 0 | 0 | 1.000 | 1.000 |

Precision and recall are also 1.000 for each kind separately at every size.

Negatives:

| Assets | Within-tolerance motions (jitter, incl. 10 just under 2 m) | Falsely flagged | Near-threshold moves just over 2 m | Detected |
|---:|---:|---:|---:|---:|
| 1,000 | 190 | 0 | 10 | 10 |
| 5,000 | 910 | 0 | 10 | 10 |
| 10,000 | 1,810 | 0 | 10 | 10 |

How to read this: perfect scores mean the engine implements its own rules exactly. The
generator was written alongside the engine, so this is a consistency check, not an
independent benchmark. The independent checks are:

- the hand-authored golden pair (`fixtures/golden/`): the expected change set and the
  expected reviewed output were written by hand, before the engine produced them;
- a GDAL/GeoPandas cross-read of the golden files and a UTM planar distance check that
  agrees with the geodesic to within 0.1% (`test_independent_geopandas.py`);
- the manual QGIS procedure in [QGIS_VERIFY.md](QGIS_VERIFY.md). **It has not been run**:
  QGIS is not installed on the development machine.

These are enforced as tests: `test_manifest.py` (5,000 assets, exact component set),
`test_compare.py::test_golden_matches_hand_authored_expectation`,
`test_resolve.py::test_golden_final_export_matches_hand_authored_result`, the browser
resolver test `resolve.test.ts` (golden decisions reproduce `expected_reviewed.geojson`), and
the Playwright journey, which downloads the final export through the UI, compares it with
the golden output, then fetches the same export from the API with the browser's token.

## Engine timings (computation only)

| Assets | Input (both files) | Parse both | Compare | Resolve (draft) |
|---:|---:|---:|---:|---:|
| 1,000 | 0.81 MB | 14 ms (14–23) | 4.4 ms (4.2–6.1) | 8.4 ms (7.7–15) |
| 5,000 | 4.03 MB | 101 ms (78–109) | 31 ms (29–55) | 45 ms (42–71) |
| 10,000 | 8.07 MB | 308 ms (234–333) | 78 ms (75–159) | 142 ms (85–163) |

Parse includes validation (geometry, coordinate ranges, duplicate IDs, ID-field candidates).

## API timings (loopback HTTP + PostGIS, client-observed)

Median (min–max) over 5 fresh workspaces. Network floor: 2.2 ms per request.

| Step | 1,000 assets (119 changes) | 5,000 assets (543 changes) |
|---|---:|---:|
| Upload baseline | 46 ms (29–74) | 152 ms (125–270) |
| Upload proposed | 32 ms (29–58) | 141 ms (127–198) |
| Compare (store results) | 74 ms (70–423) | 376 ms (358–665) |
| First queue page (200 assets) | 12 ms (12–14) | 38 ms (37–40) |
| Whole queue (1 / 3 pages) | 12 ms (11–27) | 132 ms (129–143) |
| Map, full extent | 11 ms (10–14) | 91 ms (90–98) |
| Asset detail | 5.8 ms (4.2–9.1) | 5.1 ms (4.7–5.4) |
| One decision | 6.0 ms (5.6–10.7) | 24 ms (22–26) |
| Draft export (GeoJSON + report) | 40 ms (36–54) | 182 ms (153–193) |

Run-to-run spread is real. An earlier run of the same script, before the cold-start
restarts and without status assertions, measured compare at a median of 131 ms and 587 ms (5,000-asset max 2.1 s). The
table and JSON are from the second, committed run.
Treat differences under about 2× between runs as noise on this machine.

Memory: after the first benchmark run the containers used 58 MiB (API), 104 MiB (PostGIS) and 8 MiB
(nginx), per `docker stats`.

## Cold start

`docker compose restart api` → `/ready`: median 4.45 s (4.16–5.33), 3 runs.

## Browser

The guided sample makes no API calls (verified by aborting every API request in Playwright).
The bundle is 0.34 MB of JSON precomputed by the Python engine at 1, 2 and 5 m. Browser
resolver output matches the Python output for a mixed decision set at all three presets.
