# Comparison engine

`api/geodiff/engine/` is the only place that decides what counts as a change. The API, the CLI
and the static demo fixture all call it. Output schemas: `geodiff.comparison/1` (evidence and
patches) and `geodiff.report/1` (export report).

## Input contract

- RFC 7946 GeoJSON `FeatureCollection` of 2D `Point` features, WGS84 longitude/latitude.
- Rejected with an issue code and remediation guidance: invalid JSON (`invalid_json`), wrong
  top level (`not_feature_collection`), non-Feature elements (`not_feature`), `null_geometry`,
  `unsupported_geometry` (anything but Point, including MultiPoint), `invalid_coordinates`,
  `z_coordinate`, NaN/Infinity anywhere (`nonfinite_value`), longitude outside [-180, 180] or
  latitude outside [-90, 90] (`coordinate_out_of_range`), a declared non-WGS84 `crs`
  (`conflicting_crs`), and more than the configured feature limit (`too_many_features`).
- A legacy `crs` member naming CRS84 or EPSG:4326 is accepted with a warning.
- **Coordinate ranges cannot certify a CRS.** Projected data with small values (for example
  a local engineering grid) can fall inside [-180, 180]. GeoDiff trusts RFC 7946's WGS84
  default; the analyst must make sure exports are EPSG:4326. To reproject in QGIS, right-click
  the layer, choose Export > Save Features As…, set Format = GeoJSON and CRS = EPSG:4326 - WGS 84.
- Issues are counted exactly per code, but only the first 20 per code are returned as samples.

## ID policy

Assets are matched only by the reviewer's chosen ID field (which may differ per version), never
by array position.

| Raw value | Normalized | Notes |
| --- | --- | --- |
| `"BENCH-0142"` | `BENCH-0142` | Unicode NFC, surrounding whitespace trimmed |
| `"0142"` | `0142` | leading zeros are significant: `"0142"` ≠ `142` |
| `142`, `142.0` | `142` | integral numbers become canonical decimal |
| `"bench-1"` vs `"BENCH-1"` | different | case-sensitive |
| `null`, `""`, `"  "` | rejected | `null_id`, `empty_id` |
| `true`, `1.5`, `{…}`, `[…]` | rejected | `unsupported_id_type` |

- Within one version, a repeated raw value is a `duplicate_id`. Two raw values that normalize
  to the same ID (`142` and `"142"`) are an `ambiguous_id`. Both block comparison.
- Across versions, a numeric `142` matches a string `"142"`, with an `id_type_mismatch`
  warning.
- A changed ID is an addition plus a deletion.
- If fewer than 50% of the smaller version's IDs match, a `low_id_overlap` warning appears. A
  `leading_zero_hint` is added when ignoring leading zeros would fix most mismatches.

## Fields: compared, ignored, preserved

- **Compared:** the fields the reviewer selects. Only scalar values (string, number, boolean,
  null) can be compared, and choosing a field that holds objects or arrays is an error
  (`nested_compare_field`).
- **Never compared:** the chosen ID fields (`id_field_compared`).
- **Not suggested by default:** importer and editor bookkeeping fields such as `imported_at`,
  `created_date`, `last_edited_date`, `EditDate`, `GlobalID` and `OBJECTID`. A reviewer can
  still select them explicitly.
- **Preserved:** every unselected property, including nested objects, comes from the approved
  baseline. Unselected changes in the proposed file are ignored.
- **Equality:** JSON types stay distinct. `"5"` ≠ `5`, `true` ≠ `1`, and `null` ≠ absent.
  Integers and floats are both JSON numbers, so `2010` = `2010.0`.

## Movement

- Distance uses `pyproj.Geod(ellps="WGS84").inv`, Karney's geodesic algorithm, in meters on
  the WGS84 ellipsoid. A test cross-checks it against PostGIS
  `ST_Distance(geography, geography, true)` to within 1 mm.
- A move is reviewable when `distance_m > tolerance_m` (strict). Classification uses full
  precision; only display rounds.
- Identical coordinates are exactly 0 m. The vectorized solver otherwise returns about 1e-10 m.
- Sub-threshold motion is not a change. The export keeps the baseline position, and
  `summary.within_tolerance_moves` counts these cases.
- Tests cover zero distance, a 1 cm move, a high-latitude case (one degree of longitude at
  89.9° ≈ 194 m), antimeridian wraparound (179.9999° to −179.9999° ≈ 22.26 m), and exact
  equality with the tolerance.

## Components and patches

| Kind | When | Patch |
| --- | --- | --- |
| `addition` | ID only in proposed | `{"op":"insert","feature":…}` (rekeyed to the baseline ID field) |
| `deletion` | ID only in baseline | `{"op":"remove"}` |
| `move` | matched, distance > tolerance | `{"op":"set_coordinates","coordinates":[lon,lat]}` |
| `attribute` | matched, selected field differs | `{"op":"set_property",…}` or `{"op":"delete_property",…}` |

Component IDs are deterministic: `<asset>:addition`, `<asset>:deletion`, `<asset>:move` and
`<asset>:attr:<field>`. Components are grouped under their asset, and assets are sorted by ID
in Unicode code point order. The summary reports affected assets, components (total and by
kind), matched and unchanged assets, and ID overlap.

## Resolution

| Disposition | Effect |
| --- | --- |
| `accepted` | apply the patch |
| `rejected` | keep the baseline |
| `pending` | keep the baseline; unresolved |
| `flagged` | keep the baseline; unresolved; a note is required |

- Accepting an addition inserts it. A pending, flagged or rejected addition is omitted, and the
  report explains each omission.
- Accepting a deletion removes the feature. Any other disposition keeps it.
- A final export requires zero unresolved components. A draft export is always available and
  lists unresolved components under `unresolved`.
- Output features are sorted by asset ID.
- The report (`geodiff.report/1`) holds input names and SHA-256 hashes, attribution, tolerance
  and its rule, units, distance method, ID fields, compared fields, counts by disposition and
  kind, notes, each decision with its comment and timestamp, and the review history.

## Named example

BENCH-0142 is a bench in the synthetic Pecan Hollow Park. The approved position is
(-96.7969, 32.78434). The field report places it at (-96.79676, 32.785412), across 11th St in a
residential block. The condition also changes from `good` to `damaged`.

- Measured move: **119.607 m**, bearing about 6° (Karney geodesic, WGS84). PostGIS geography
  agrees to within 1 mm.
- Intended review: accept the condition, reject the move. The export keeps the bench at its
  approved position with `condition: "damaged"`.

## Fixtures

- `fixtures/golden/`: a hand-authored 13-feature pair with expected changes, decisions and
  expected reviewed export (all written by hand from the design).
- `fixtures/invalid/`: one file per validation failure (`scripts/make_invalid_fixtures.py`).
- Synthetic 5,000-asset pair (`make fixtures`): `geodiff generate --out ../fixtures`, seed
  20261118. This writes `fixtures/synthetic/{baseline,proposed}_5000.geojson` and an
  independent `manifest_5000.json`. The files are deterministic for a given platform and are not
  committed. The tests regenerate them in memory.
- `fixtures/context/`: synthetic streets, blocks, parks and paths for the map.

All fixtures are **synthetic**. The fictional Cedar Hollow Parks Department and its street grid
near Dallas, TX are invented, and no change is a real report from any city.

## CLI

```sh
cd api
uv run geodiff validate ../fixtures/golden/baseline.geojson
uv run geodiff compare ../fixtures/golden/baseline.geojson ../fixtures/golden/proposed.geojson \
  --id-field asset_id --fields condition,install_year,material,notes --tolerance 2 --out evidence.json
uv run geodiff resolve ../fixtures/golden/baseline.geojson evidence.json \
  ../fixtures/golden/decisions.json --final --out reviewed.geojson --report report.json
```

Exit codes: 0 OK, 2 invalid input, 3 final export blocked by unresolved components.
