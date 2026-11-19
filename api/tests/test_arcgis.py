"""Offline tests for the curated ArcGIS importer (a fake server via httpx.MockTransport).

A live check against the real source runs only with GEODIFF_NETWORK=1.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from geodiff import arcgis
from geodiff.engine.geojson import parse_geojson

LAYER = "https://gis.example.gov/arcgis/rest/services/Parks/FeatureServer/0"
SRC = arcgis.Source(
    id="test-parks",
    title="Test parks",
    owner="Example County",
    layer_url=LAYER,
    item_url="https://example.gov/item",
    id_field="GlobalID",
    id_note="GlobalID is persistent.",
    reuse="Public domain (test).",
    attribution="Example County Parks",
    verified_on="2026-11-18",
    verified_facts="",
    suggested_compare_fields=("NAME",),
)


def feats(n: int, start: int = 1) -> list[dict[str, Any]]:
    return [
        {
            "OBJECTID": i,
            "GlobalID": f"g-{i:04d}",
            "NAME": f"Bench {i}",
            "x": -96.8 + i * 1e-4,
            "y": 32.78 + i * 1e-4,
        }
        for i in range(start, start + n)
    ]


class FakeLayer:
    def __init__(self, rows: list[dict[str, Any]], **meta: Any) -> None:
        self.rows = rows
        self.meta = {
            "geometryType": "esriGeometryPoint",
            "capabilities": "Query",
            "objectIdField": "OBJECTID",
            "maxRecordCount": 2,
            "supportedQueryFormats": "JSON, geoJSON, PBF",
            "fields": [{"name": "OBJECTID"}, {"name": "GlobalID"}, {"name": "NAME"}],
            "editingInfo": {"lastEditDate": 1_790_000_000_000},
        } | meta
        self.calls: list[tuple[str, dict[str, list[str]]]] = []
        self.script: list[httpx.Response | Exception] = []  # responses to inject first
        self.on_query: Any = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = (
            parse_qs(request.content.decode())
            if request.method == "POST"
            else parse_qs(request.url.query.decode())
        )
        self.calls.append((request.url.path, params))
        if self.script:
            r = self.script.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        if request.url.path.endswith("/0"):
            return httpx.Response(200, json=self.meta)
        if self.on_query:
            self.on_query(self, params)
        if params.get("returnIdsOnly") == ["true"]:
            return httpx.Response(200, json={"objectIds": [r["OBJECTID"] for r in self.rows]})
        want = {int(x) for x in params["objectIds"][0].split(",")}
        rows = [r for r in self.rows if r["OBJECTID"] in want]
        if params["f"] == ["geojson"]:
            return httpx.Response(
                200,
                json={
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "id": r["OBJECTID"],
                            "geometry": {"type": "Point", "coordinates": [r["x"], r["y"]]},
                            "properties": {k: r[k] for k in ("OBJECTID", "GlobalID", "NAME")},
                        }
                        for r in rows
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "attributes": {k: r[k] for k in ("OBJECTID", "GlobalID", "NAME")},
                        "geometry": {"x": r["x"], "y": r["y"], "spatialReference": {"wkid": 4326}},
                    }
                    for r in rows
                ]
            },
        )


def importer(layer: FakeLayer) -> arcgis.Importer:
    client = httpx.Client(transport=httpx.MockTransport(layer.handler), follow_redirects=False)
    return arcgis.Importer(
        client, sleep=lambda _s: None, now=lambda: datetime(2026, 11, 18, 12, tzinfo=UTC)
    )


def test_pages_in_bounded_batches_and_records_provenance() -> None:
    layer = FakeLayer(feats(5))
    data, rec = importer(layer).snapshot(SRC, max_features=100)
    fc = json.loads(data)
    assert [f["properties"]["GlobalID"] for f in fc["features"]] == [
        f"g-{i:04d}" for i in range(1, 6)
    ]
    queries = [p for path, p in layer.calls if path.endswith("/query") and "objectIds" in p]
    assert [q["objectIds"][0] for q in queries] == ["1,2", "3,4", "5"]  # maxRecordCount 2
    assert all(q["outSR"] == ["4326"] and q["f"] == ["geojson"] for q in queries)
    assert rec["attribution"] == "Example County Parks"
    assert rec["feature_count"] == 5
    assert rec["retrieved_at"] == "2026-11-18T12:00:00+00:00"
    assert rec["service_last_edit"].startswith("2026-")
    assert rec["id_field"] == "GlobalID"
    ds = parse_geojson(data, name="x.geojson")
    assert ds.valid and "GlobalID" in ds.summary()["candidate_id_fields"]


def test_esri_json_is_normalized_when_geojson_is_unsupported() -> None:
    layer = FakeLayer(feats(3), supportedQueryFormats="JSON")
    data, rec = importer(layer).snapshot(SRC, max_features=100)
    fc = json.loads(data)
    geom = fc["features"][0]["geometry"]
    assert geom["type"] == "Point"
    assert geom["coordinates"] == pytest.approx([-96.7999, 32.7801])
    assert rec["query"]["format"] == "esrijson"


def test_service_limit_and_feature_cap() -> None:
    with pytest.raises(arcgis.ArcGISError) as e:
        importer(FakeLayer(feats(5))).snapshot(SRC, max_features=4)
    assert e.value.code == "source_too_large"


@pytest.mark.parametrize(
    ("meta", "code"),
    [
        ({"geometryType": "esriGeometryPolygon"}, "unsupported_geometry"),
        ({"capabilities": "Map"}, "query_unsupported"),
        ({"fields": [{"name": "OBJECTID"}]}, "id_field_missing"),
    ],
)
def test_metadata_checks(meta: dict[str, Any], code: str) -> None:
    with pytest.raises(arcgis.ArcGISError) as e:
        importer(FakeLayer(feats(2), **meta)).snapshot(SRC, max_features=10)
    assert e.value.code == code


def test_transient_errors_retry_then_succeed() -> None:
    layer = FakeLayer(feats(2))
    layer.script = [httpx.Response(503), httpx.ConnectTimeout("slow")]
    data, _ = importer(layer).snapshot(SRC, max_features=10)
    assert len(json.loads(data)["features"]) == 2


def test_persistent_failure_reports_unavailable() -> None:
    layer = FakeLayer(feats(2))
    layer.script = [httpx.Response(502)] * 3
    with pytest.raises(arcgis.ArcGISError) as e:
        importer(layer).snapshot(SRC, max_features=10)
    assert e.value.code == "source_unavailable"


def test_http_404_redirect_token_and_error_bodies() -> None:
    cases: list[tuple[httpx.Response, str]] = [
        (httpx.Response(404), "source_http_error"),
        (httpx.Response(302, headers={"Location": "https://evil.example/"}), "redirect_rejected"),
        (
            httpx.Response(200, json={"error": {"code": 499, "message": "Token Required"}}),
            "source_not_public",
        ),
        (
            httpx.Response(200, json={"error": {"code": 400, "message": "bad"}}),
            "source_query_error",
        ),
        (httpx.Response(200, text="<html>"), "source_bad_response"),
    ]
    for resp, code in cases:
        layer = FakeLayer(feats(2))
        layer.script = [resp]
        with pytest.raises(arcgis.ArcGISError) as e:
            importer(layer).snapshot(SRC, max_features=10)
        assert e.value.code == code, code


def test_source_drift_retries_once_then_commits_a_complete_snapshot() -> None:
    layer = FakeLayer(feats(4))
    state = {"n": 0}

    def drift(lay: FakeLayer, params: dict[str, list[str]]) -> None:
        # First pass: a feature is deleted after the ID list was read.
        if "objectIds" in params and state["n"] == 0:
            state["n"] = 1
            lay.rows = [r for r in lay.rows if r["OBJECTID"] != 3]

    layer.on_query = drift
    data, rec = importer(layer).snapshot(SRC, max_features=10)
    assert rec["feature_count"] == 3
    assert len(json.loads(data)["features"]) == 3


def test_continuous_drift_fails_without_partial_data() -> None:
    layer = FakeLayer(feats(4))

    def drift(lay: FakeLayer, params: dict[str, list[str]]) -> None:
        if "objectIds" in params:
            lay.rows = [
                r for r in lay.rows if r["OBJECTID"] != max(x["OBJECTID"] for x in lay.rows)
            ]

    layer.on_query = drift
    with pytest.raises(arcgis.ArcGISError) as e:
        importer(layer).snapshot(SRC, max_features=10)
    assert e.value.code == "source_changed"


def test_requests_outside_the_layer_are_refused() -> None:
    other = arcgis.Source(**{**SRC.__dict__, "layer_url": LAYER})
    imp = importer(FakeLayer(feats(1)))
    with pytest.raises(arcgis.ArcGISError) as e:
        imp._call(other, "https://gis.example.gov/other/FeatureServer/0/query", {}, post=True)
    assert e.value.code == "source_not_allowed"


def test_manifest_requires_plain_https(tmp_path: Path) -> None:
    good = json.loads(arcgis.MANIFEST_PATH.read_text())
    assert arcgis.load_manifest()  # the committed manifest is valid
    for bad in ("http://x.gov/a/FeatureServer/0", "https://x.gov/a/0?token=abc"):
        m = {"sources": [good["sources"][0] | {"layer_url": bad}]}
        p = tmp_path / "s.json"
        p.write_text(json.dumps(m))
        with pytest.raises(ValueError):
            arcgis.load_manifest(p)


@pytest.mark.skipif(os.environ.get("GEODIFF_NETWORK") != "1", reason="set GEODIFF_NETWORK=1")
def test_live_curated_source() -> None:  # pragma: no cover - network
    src = arcgis.sources()["nps-yose-poi"]
    data, rec = arcgis.Importer().snapshot(src, max_features=10_000)
    ds = parse_geojson(data, name="yose.geojson")
    assert ds.valid
    assert "GlobalID" in ds.summary()["candidate_id_fields"]
    print(json.dumps({k: rec[k] for k in ("retrieved_at", "feature_count", "service_last_edit")}))
