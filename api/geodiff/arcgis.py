"""Read-only snapshot importer for curated, explicitly public ArcGIS point layers.

Safety model
- The client sends only a source ID. Layer URLs come from ``sources.json`` (server config) and
  must be HTTPS on an allowlisted host; redirects are refused, never followed.
- No token is ever sent; a layer that asks for one is reported as not public.
- Only read endpoints are called (layer metadata and ``query``). GeoDiff never writes to a source.

Snapshot procedure
1. Read layer metadata: geometry type, query capability, fields, object-ID field, max record
   count, supported formats, last edit date.
2. ``returnIdsOnly`` gives the full, sorted object-ID list (the expected set).
3. Query bounded batches by explicit object IDs with ``outSR=4326`` (GeoJSON when supported,
   otherwise Esri JSON normalized here).
4. Retrieved IDs must equal the expected set. If the source changed mid-import, retry the whole
   snapshot once; otherwise fail. Only a complete snapshot is returned.

Transient failures (timeouts, connection errors, HTTP 429/5xx, ArcGIS 5xx error bodies) retry
with bounded exponential backoff.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

MANIFEST_PATH = Path(__file__).with_name("sources.json")
USER_AGENT = "GeoDiff/0.1 (read-only snapshot importer)"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
BATCH_MAX = 500
ATTEMPTS = 3
BACKOFF_S = (0.5, 1.5)


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    owner: str
    layer_url: str
    item_url: str
    id_field: str
    id_note: str
    reuse: str
    attribution: str
    verified_on: str
    verified_facts: str
    suggested_compare_fields: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "owner": self.owner,
            "layer_url": self.layer_url,
            "item_url": self.item_url,
            "id_field": self.id_field,
            "id_note": self.id_note,
            "reuse": self.reuse,
            "attribution": self.attribution,
            "verified_on": self.verified_on,
            "suggested_compare_fields": list(self.suggested_compare_fields),
        }


class ArcGISError(Exception):
    def __init__(self, code: str, message: str, guidance: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.guidance = guidance
        self.status = status


class _Retryable(Exception):
    pass


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Source]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, Source] = {}
    for s in raw["sources"]:
        src = Source(
            **{k: s[k] for k in Source.__dataclass_fields__ if k != "suggested_compare_fields"},
            suggested_compare_fields=tuple(s.get("suggested_compare_fields", [])),
        )
        parts = urlsplit(src.layer_url)
        if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
            raise ValueError(f"Source {src.id}: layer_url must be a plain HTTPS URL")
        out[src.id] = src
    return out


@lru_cache
def sources() -> dict[str, Source]:
    return load_manifest()


def _allowed(url: str, src: Source) -> bool:
    """Requests may only go to the configured layer URL or its /query endpoint."""
    want = urlsplit(src.layer_url)
    got = urlsplit(url)
    return (
        got.scheme == "https"
        and got.hostname == want.hostname
        and got.port == want.port
        and got.path in (want.path, want.path + "/query")
    )


class Importer:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client or httpx.Client(
            timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT}
        )
        self.sleep = sleep
        self.now = now

    # ------------------------------------------------------------------ HTTP

    def _call(self, src: Source, url: str, params: dict[str, str], *, post: bool) -> dict[str, Any]:
        if not _allowed(url, src):
            raise ArcGISError(
                "source_not_allowed", "Refused a request outside the configured layer.", ""
            )
        last: Exception | None = None
        for attempt in range(ATTEMPTS):
            try:
                return self._once(url, params, post=post)
            except _Retryable as exc:
                last = exc
                if attempt < ATTEMPTS - 1:
                    self.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
        raise ArcGISError(
            "source_unavailable",
            f"The public layer did not respond after {ATTEMPTS} attempts ({last}).",
            "The source server may be busy or down. Try the import again later.",
        )

    def _once(self, url: str, params: dict[str, str], *, post: bool) -> dict[str, Any]:
        try:
            r = self.client.post(url, data=params) if post else self.client.get(url, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise _Retryable(type(exc).__name__) from exc
        if r.is_redirect or 300 <= r.status_code < 400:
            raise ArcGISError(
                "redirect_rejected",
                "The public layer redirected to another location; GeoDiff does not follow redirects.",
                "The source may have moved. The curated source list needs to be re-verified.",
            )
        if r.status_code == 429 or r.status_code >= 500:
            raise _Retryable(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise ArcGISError(
                "source_http_error",
                f"The public layer returned HTTP {r.status_code}.",
                "The source may have moved or been restricted.",
            )
        if len(r.content) > MAX_RESPONSE_BYTES:
            raise ArcGISError("source_response_too_large", "A source response exceeded 20 MB.", "")
        try:
            body = r.json()
        except ValueError as exc:
            raise ArcGISError(
                "source_bad_response", "The public layer did not return JSON.", ""
            ) from exc
        if not isinstance(body, dict):
            raise ArcGISError("source_bad_response", "Unexpected response from the layer.", "")
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            if code in (498, 499, 401, 403):
                raise ArcGISError(
                    "source_not_public",
                    "The layer requires a token or sign-in.",
                    "Only explicitly public layers can be imported.",
                )
            if isinstance(code, int) and code >= 500:
                raise _Retryable(f"ArcGIS error {code}")
            raise ArcGISError(
                "source_query_error",
                f"The layer rejected the query: {str(err.get('message'))[:200]}",
                "",
            )
        return body

    # ------------------------------------------------------------------ snapshot

    def metadata(self, src: Source) -> dict[str, Any]:
        m = self._call(src, src.layer_url, {"f": "json"}, post=False)
        if m.get("geometryType") != "esriGeometryPoint":
            raise ArcGISError(
                "unsupported_geometry",
                f"The layer has {m.get('geometryType')!r} geometry; GeoDiff compares points.",
                "Choose a point layer.",
                status=422,
            )
        caps = {c.strip().lower() for c in str(m.get("capabilities", "")).split(",")}
        if "query" not in caps:
            raise ArcGISError("query_unsupported", "The layer does not allow queries.", "")
        fields = {f.get("name") for f in m.get("fields") or []}
        if src.id_field not in fields:
            raise ArcGISError(
                "id_field_missing",
                f"The layer no longer has the ID field {src.id_field!r}.",
                "The source schema changed; the curated source needs re-verification.",
            )
        if not m.get("objectIdField"):
            raise ArcGISError("source_bad_response", "The layer has no object-ID field.", "")
        return m

    def snapshot(self, src: Source, *, max_features: int) -> tuple[bytes, dict[str, Any]]:
        meta = self.metadata(src)
        for attempt in range(2):
            try:
                return self._snapshot_once(src, meta, max_features=max_features)
            except _SourceChanged:
                if attempt == 1:
                    raise ArcGISError(
                        "source_changed",
                        "The layer changed while it was being read.",
                        "The source is being edited right now. Try again in a few minutes.",
                    ) from None
        raise AssertionError("unreachable")

    def _snapshot_once(
        self, src: Source, meta: dict[str, Any], *, max_features: int
    ) -> tuple[bytes, dict[str, Any]]:
        oid_field = str(meta["objectIdField"])
        query_url = src.layer_url + "/query"
        ids_body = self._call(
            src, query_url, {"where": "1=1", "returnIdsOnly": "true", "f": "json"}, post=True
        )
        ids = sorted(int(i) for i in ids_body.get("objectIds") or [])
        if not ids:
            raise ArcGISError("source_empty", "The layer returned no features.", "", status=422)
        if len(ids) > max_features:
            raise ArcGISError(
                "source_too_large",
                f"The layer has {len(ids):,} features; the limit is {max_features:,}.",
                "Choose a smaller layer.",
                status=422,
            )
        max_rc = int(meta.get("maxRecordCount") or 1000)
        batch = max(1, min(BATCH_MAX, max_rc))
        geojson = "geojson" in str(meta.get("supportedQueryFormats", "")).lower()
        features: dict[int, dict[str, Any]] = {}
        for i in range(0, len(ids), batch):
            chunk = ids[i : i + batch]
            body = self._call(
                src,
                query_url,
                {
                    "objectIds": ",".join(map(str, chunk)),
                    "outFields": "*",
                    "outSR": "4326",
                    "returnGeometry": "true",
                    "f": "geojson" if geojson else "json",
                },
                post=True,
            )
            if body.get("exceededTransferLimit") or (body.get("properties") or {}).get(
                "exceededTransferLimit"
            ):
                raise _SourceChanged
            got = _normalize(body, oid_field, geojson=geojson)
            for oid, feat in got:
                features[oid] = feat
        if sorted(features) != ids:
            raise _SourceChanged
        fc = {"type": "FeatureCollection", "features": [features[o] for o in ids]}
        data = json.dumps(fc, ensure_ascii=False, separators=(",", ":")).encode()
        retrieved = self.now().isoformat(timespec="seconds")
        last_edit = (meta.get("editingInfo") or {}).get("lastEditDate")
        record = {
            "type": "arcgis_feature_layer",
            "source_id": src.id,
            "title": src.title,
            "owner": src.owner,
            "layer_url": src.layer_url,
            "item_url": src.item_url,
            "attribution": src.attribution,
            "reuse": src.reuse,
            "retrieved_at": retrieved,
            "feature_count": len(ids),
            "id_field": src.id_field,
            "id_note": src.id_note,
            "object_id_field": oid_field,
            "object_ids_sha256": hashlib.sha256(",".join(map(str, ids)).encode()).hexdigest(),
            "service_last_edit": (
                datetime.fromtimestamp(int(last_edit) / 1000, UTC).isoformat(timespec="seconds")
                if last_edit
                else None
            ),
            "query": {
                "outSR": 4326,
                "format": "geojson" if geojson else "esrijson",
                "batch": batch,
            },
        }
        return data, record


class _SourceChanged(Exception):
    pass


def _normalize(
    body: dict[str, Any], oid_field: str, *, geojson: bool
) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    if geojson:
        for f in body.get("features") or []:
            props = dict(f.get("properties") or {})
            oid = props.get(oid_field, f.get("id"))
            geom = f.get("geometry")
            out.append((int(oid), {"type": "Feature", "geometry": geom, "properties": props}))
        return out
    for f in body.get("features") or []:
        attrs = dict(f.get("attributes") or {})
        g = f.get("geometry")
        geom = None
        if isinstance(g, dict) and g.get("x") is not None and g.get("y") is not None:
            geom = {"type": "Point", "coordinates": [g["x"], g["y"]]}
        out.append(
            (int(attrs[oid_field]), {"type": "Feature", "geometry": geom, "properties": attrs})
        )
    return out
