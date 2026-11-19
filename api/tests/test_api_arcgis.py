"""ArcGIS import through the API (fake source server, real PostGIS)."""

from __future__ import annotations

import httpx
import pytest

from geodiff import arcgis
from geodiff.main import app
from geodiff.migrate import apply_migrations
from tests.test_api import new_ws
from tests.test_arcgis import SRC, FakeLayer, feats

pytestmark = pytest.mark.db


@pytest.fixture(scope="module", autouse=True)
def migrated():
    apply_migrations()


@pytest.fixture
def layer(monkeypatch):
    lay = FakeLayer(feats(5))
    monkeypatch.setattr(arcgis, "sources", lambda: {SRC.id: SRC})
    real = arcgis.Importer

    def make(*_a, **_k):
        client = httpx.Client(transport=httpx.MockTransport(lay.handler), follow_redirects=False)
        return real(client, sleep=lambda _s: None)

    monkeypatch.setattr(arcgis, "Importer", make)
    return lay


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


def test_sources_are_listed_without_auth(client, layer):
    r = client.get("/v1/sources")
    assert r.status_code == 200
    (s,) = r.json()["sources"]
    assert s["id"] == SRC.id and s["layer_url"] == SRC.layer_url and s["reuse"]


def test_import_snapshot_records_source_and_compares(client, layer):
    h, _ = new_ws(client)
    r = client.post("/v1/datasets/arcgis", headers=h, json={"source_id": SRC.id})
    assert r.status_code == 201, r.text
    d1 = r.json()
    assert d1["feature_count"] == 5 and d1["valid"]
    assert d1["source"]["attribution"] == "Example County Parks"
    assert d1["source"]["layer_url"] == SRC.layer_url
    assert "GlobalID" in d1["summary"]["candidate_id_fields"]
    # The source changes: one feature renamed, one added. A second snapshot differs.
    layer.rows[0]["NAME"] = "Bench 1 (renamed)"
    layer.rows.append(feats(1, start=6)[0])
    d2 = client.post("/v1/datasets/arcgis", headers=h, json={"source_id": SRC.id}).json()
    assert d2["dataset_id"] != d1["dataset_id"] and d2["feature_count"] == 6
    r = client.post(
        "/v1/comparisons",
        headers=h,
        json={
            "baseline_dataset_id": d1["dataset_id"],
            "proposed_dataset_id": d2["dataset_id"],
            "baseline_id_field": "GlobalID",
            "compared_fields": ["NAME"],
            "tolerance_m": 2,
        },
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["summary"]["components_by_kind"] == {
        "addition": 1,
        "deletion": 0,
        "move": 0,
        "attribute": 1,
    }
    assert c["inputs"]["baseline"]["source"]["source_id"] == SRC.id
    # Unchanged source: identical bytes are deduplicated.
    layer.rows.pop()
    layer.rows[0]["NAME"] = "Bench 1"
    again = client.post("/v1/datasets/arcgis", headers=h, json={"source_id": SRC.id})
    assert again.status_code == 200 and again.json()["dataset_id"] == d1["dataset_id"]


def test_failed_import_stores_nothing(client, layer):
    h, _ = new_ws(client)
    layer.script = [httpx.Response(200, json={"error": {"code": 499, "message": "Token Required"}})]
    r = client.post("/v1/datasets/arcgis", headers=h, json={"source_id": SRC.id})
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "source_not_public"
    assert client.get("/v1/datasets", headers=h).json()["datasets"] == []


def test_unknown_source_and_no_url_injection(client, layer):
    h, _ = new_ws(client)
    r = client.post("/v1/datasets/arcgis", headers=h, json={"source_id": "https://evil.example/x"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_source"
    r = client.post(
        "/v1/datasets/arcgis",
        headers=h,
        json={"source_id": SRC.id, "layer_url": "https://evil.example/"},
    )
    assert r.status_code == 201  # extra fields are ignored; only the curated URL is used
    assert {p for p, _ in layer.calls} <= {
        "/arcgis/rest/services/Parks/FeatureServer/0",
        "/arcgis/rest/services/Parks/FeatureServer/0/query",
    }


def test_import_requires_workspace(client, layer):
    assert client.post("/v1/datasets/arcgis", json={"source_id": SRC.id}).status_code == 401
