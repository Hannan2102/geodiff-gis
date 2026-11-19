# Public data sources

GeoDiff can snapshot **curated, explicitly public ArcGIS point layers** into a live workspace.
The browser sends only a source ID; the server resolves it from
[`api/geodiff/sources.json`](../api/geodiff/sources.json). Arbitrary URLs are never accepted.

## Curated sources

| ID | Layer | Owner | Reuse basis | Verified |
|---|---|---|---|---|
| `nps-yose-poi` | [Yosemite National Park: Points of Interest](https://www.arcgis.com/home/item.html?id=dc050222c1ee45f3b5e366bdc2cebfa2) ([REST layer](https://services1.arcgis.com/fBc8EJBxQRMcHlei/arcgis/rest/services/YOSE_PointsOfInterest/FeatureServer/0)) | National Park Service, Yosemite National Park GIS | Work of the U.S. federal government (17 U.S.C. 105), so no U.S. copyright. The item's license field contains the NPS data disclaimer: no warranty, and obtain data directly from an NPS server, which the importer does. | 2026-11-18 |

What was checked on 2026-11-18: public item; service capabilities `Query` only (no editing);
`esriGeometryPoint`; 552 features; `maxRecordCount` 1000; GeoJSON query format supported; item
modified 2026-06-01; service last edited 2026-11-18 (from a live snapshot).

### ID choice

- **`GlobalID`** is the asset ID: unique and never null across all 552 features at verification.
- **`OBJECTID`** is assigned by the server and can change if the layer is republished. GeoDiff
  uses it only to page through the layer.
- **`FEATUREID`** had 15 duplicate values, so GeoDiff's ID validation rejects it as an asset ID.
  This is a real example of why IDs are validated before comparison.

### Candidates rejected

| Layer | Reason |
|---|---|
| Fairfax County Park Amenity Locator | Terms reserve all rights and allow reproduction only for internal or personal use. |
| Austin PARD amenity points | The item license is an accuracy disclaimer; no reuse license found. |
| Fort Worth parks assets, The Woodlands amenities | Services expose editing capabilities; avoided for a public demo. |
| Dallas park layers | Only a polygon park-boundary layer was found; no amenity points. |

## What a snapshot means

A snapshot is the published layer at one moment. Comparing an earlier snapshot with a later one
shows how the published layer changed between those times. These are **not** changes verified
or reported by GeoDiff, and the guided sample's edits are synthetic, not changes reported by any
agency.

## Importer behavior ([`arcgis.py`](../api/geodiff/arcgis.py))

1. Only the configured HTTPS host and layer path (and its `/query`) can be requested.
   Redirects are refused, no token is ever sent, and a layer that requires one is reported
   as not public.
2. The importer reads metadata and checks point geometry, the `Query` capability, the ID
   field and the object-ID field.
3. It requests `returnIdsOnly` for the expected ID set, then queries batches of
   `min(500, maxRecordCount)` by explicit object IDs with `outSR=4326`. GeoJSON is used when
   supported; otherwise Esri JSON is normalized.
4. The retrieved IDs must equal the expected set. If the source changed mid-import, the whole
   snapshot is retried once and then fails. Only complete snapshots are stored.
5. Each request has a 20 s timeout. Timeouts, connection errors, HTTP 429/5xx and ArcGIS 5xx
   error bodies are retried 3 times with backoff.
6. Provenance is stored with the dataset and carried into comparison reports: owner,
   attribution, reuse basis, URLs, retrieval time, service last-edit time, feature count, ID
   notes, and a SHA-256 of the object-ID list.

Offline tests: `api/tests/test_arcgis.py` and `api/tests/test_api_arcgis.py` use a fake server
covering paging, limits, geometry, HTTP errors, retries, redirects, tokens, drift, rollback and
URL injection. Live check: `GEODIFF_NETWORK=1 pytest tests/test_arcgis.py -k live` and
`E2E_NETWORK=1 npx playwright test -g "public layer"`.

## Adding a source

Add an entry to `sources.json` with a verified layer URL, a persistent ID field, owner,
attribution, reuse terms and a verification date. Then run the live checks above. Reject
layers without explicit public reuse terms.
