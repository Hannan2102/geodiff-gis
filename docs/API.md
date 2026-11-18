# Live workspace API (`/v1`)

The OpenAPI contract is served at `/openapi.json`, with an interactive view at `/docs`. All
responses are JSON unless an export file is requested.

## Errors

Every error uses the same envelope:

```json
{"error": {"code": "stale_decision", "message": "1 component(s) changed since this view was loaded.",
           "guidance": "Another tab or session updated these decisions. Reload the asset and decide again.",
           "request_id": "3f9c0a1b2d4e5f60", "conflicts": [...]}}
```

| Status | Codes |
| --- | --- |
| 401 | `unauthorized`, `workspace_expired` |
| 404 | `not_found`, also returned for another workspace's resources |
| 409 | `stale_decision`, `stale_revision`, `bulk_count_mismatch`, `unresolved_components`, `dataset_limit`, `comparison_limit`, `nothing_to_undo` |
| 413 | `upload_too_large` |
| 415 | `unsupported_media_type` |
| 422 | `invalid_request` (schema), `dataset_invalid`, `invalid_ids`, `comparison_invalid`, `note_required`, `scope_required`, `invalid_bbox`, `empty_upload` |
| 429 | `rate_limited` (with `Retry-After`) |
| 503 | `capacity_reached` |
| 500 | `internal_error` (no stack trace; quote the `request_id`) |

Every response carries an `X-Request-ID` header.

## Workspaces and isolation

- `POST /v1/workspaces` returns `{workspace_id, token, expires_at}`. The token (`gdw_` plus 256
  random bits) is shown once. The server stores only its SHA-256.
- Send `Authorization: Bearer <token>` on every other call. Tokens never go in URLs.
- Every query filters by the token's workspace. Other workspaces' resources return 404.
- **Expiry:** a workspace expires 72 hours (`WORKSPACE_TTL_HOURS`) after its last
  authenticated request. Expired workspaces are deleted with everything they hold when a new
  workspace is created.
- `GET /v1/workspace` returns counts and limits. `POST /v1/workspace/reset` deletes datasets and
  runs but keeps the credential. `DELETE /v1/workspace` deletes the workspace and all its data.

## Datasets

- `POST /v1/datasets?name=<file>`: send the raw GeoJSON as the body
  (`application/geo+json` or `application/json`).
  - The body is read in chunks and refused with 413 as soon as it passes `MAX_UPLOAD_BYTES`.
  - Returns 201, or 200 with `duplicate: true` when the same bytes were already uploaded to
    this workspace.
  - Invalid files are stored with their validation summary so the problems can be shown, but
    they cannot be compared.
- `GET /v1/datasets/{id}` returns the summary: feature count, SHA-256, fields with coverage
  and types, candidate ID fields, and validation results (exact counts per code plus up to 20
  sampled issues per code, each with guidance).
- `GET /v1/datasets/{id}/original` returns the exact uploaded bytes, to the owning workspace
  only.

## Comparisons

- `POST /v1/comparisons` takes
  `{baseline_dataset_id, proposed_dataset_id, baseline_id_field, proposed_id_field?, compared_fields, tolerance_m, parent_comparison_id?}`.
  - It creates immutable snapshots (dataset + ID field), stores normalized features with a
    GIST index, runs the engine, and stores components with all decisions `pending`, in one
    transaction.
  - A different tolerance or field set is a **new run**. `parent_comparison_id` records the
    lineage, and older runs keep their decisions.
- `GET /v1/comparisons` lists runs, newest first. `GET /v1/comparisons/{id}` returns
  parameters, inputs (names and hashes), summary, warnings, progress and `revision` (also sent
  as `ETag`).
- `GET /v1/comparisons/{id}/queue?kind=&disposition=&status=&q=&cursor=&limit=` returns
  assets with grouped components, keyset-paginated by asset ID. `limit` is capped at 200.
  `disposition=unresolved` means pending or flagged.
- `GET /v1/comparisons/{id}/map?bbox=minLon,minLat,maxLon,maxLat` returns changed assets
  whose old or new position is in the box, capped at 2,000 with `truncated`.
- `GET /v1/comparisons/{id}/baseline?bbox=…` returns baseline positions for map context.
- `GET /v1/comparisons/{id}/assets/{asset_id}` returns evidence: baseline and proposed values,
  components with decision and version, and the asset's history.

## Review

- `POST /v1/comparisons/{id}/decisions` takes
  `{decisions: [{component_id, disposition, note?, expected_version}]}`.
  - All items apply in one transaction.
  - If any component's version differs from `expected_version`, nothing is applied and the
    response is 409 `stale_decision`. This stops a stale tab from overwriting a newer decision.
  - `flagged` requires a note.
  - Changing your mind is just another decision, and it is audited.
- `POST /v1/comparisons/{id}/bulk/preview` takes a scope (`kind`, `field`,
  `current_disposition`, `asset_ids`, `max_distance_m`) and returns the match count and a
  sample.
- `POST /v1/comparisons/{id}/bulk` takes `{scope, disposition, note?, expected_count}`. It
  requires a non-empty scope and a count equal to the current match count.
- `POST /v1/comparisons/{id}/undo` takes `{expected_revision}` and reverts the most recent
  decision batch. The undo is itself recorded.
- `GET /v1/comparisons/{id}/history?before=&limit=` returns audit events, newest first.

## Export

`GET /v1/comparisons/{id}/export?mode=draft|final&part=geojson|report|bundle`

- `final` returns 409 `unresolved_components` (with the list) until every component is
  accepted or rejected.
- `draft` always works. Its report lists unresolved components and notes omitted pending
  additions.
- The report carries input hashes, attribution, parameters, counts, every decision with its
  note and time, the full history, and `output_sha256` of the GeoJSON part.

## Limits (defaults; environment variables)

| Setting | Default | Notes |
| --- | --- | --- |
| `MAX_UPLOAD_BYTES` | 5 MB | enforced while streaming |
| `MAX_FEATURES` | 10,000 per file | |
| `MAX_DATASETS_PER_WORKSPACE` | 12 | |
| `MAX_COMPARISONS_PER_WORKSPACE` | 30 | |
| `MAX_ACTIVE_WORKSPACES` | 200 | 503 `capacity_reached` beyond |
| `WORKSPACE_TTL_HOURS` | 72 | sliding, from last use |
| `PAGE_MAX` | 200 | queue and history pages |
| `MAP_MAX_FEATURES` | 2,000 | viewport queries |
| `RATE_LIMIT_WRITES_PER_MIN` | 120 per client IP | in-process |
| `RATE_LIMIT_WORKSPACES_PER_HOUR` | 20 per client IP | |

Memory check (2026-11-18, compose on an Apple M1): with the 5,000-asset pair the API container
went from 57 MiB idle to 91 MiB after upload, compare and export. Timings were: compare
0.60 s, 200-asset queue page 0.04 s, viewport 0.09 s, draft export 0.31 s.

## Logging

Each request logs its request ID, method, path, status and duration. Bodies, feature contents,
tokens and connection strings are never logged. Unhandled errors log a stack trace server-side
only, and the client sees `internal_error` with the request ID.

## Walkthrough

```sh
docker compose up --build -d --wait
python3 scripts/api_walkthrough.py            # steps 1–7
docker compose restart api db
python3 scripts/api_walkthrough.py --verify   # steps 8–9 after restart
python3 scripts/api_walkthrough.py --cleanup
```
