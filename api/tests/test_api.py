"""Persistent review API against a real PostGIS database."""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from fastapi.testclient import TestClient

from geodiff.db import close_pool
from geodiff.main import app
from geodiff.migrate import apply_migrations
from tests.helpers import FIXTURES, GOLDEN, GOLDEN_FIELDS

pytestmark = pytest.mark.db


@pytest.fixture(scope="module", autouse=True)
def migrated():
    apply_migrations()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def new_ws(client):
    r = client.post("/v1/workspaces")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token"].startswith("gdw_")
    return {"Authorization": f"Bearer {body['token']}"}, body


def upload(client, h, path, name=None):
    return client.post(
        "/v1/datasets",
        params={"name": name or path.name},
        content=path.read_bytes(),
        headers=h | {"Content-Type": "application/geo+json"},
    )


def golden_run(client, h, tolerance=2.0, parent=None):
    b = upload(client, h, GOLDEN / "baseline.geojson").json()
    p = upload(client, h, GOLDEN / "proposed.geojson").json()
    r = client.post(
        "/v1/comparisons",
        headers=h,
        json={
            "baseline_dataset_id": b["dataset_id"],
            "proposed_dataset_id": p["dataset_id"],
            "baseline_id_field": "asset_id",
            "compared_fields": list(GOLDEN_FIELDS),
            "tolerance_m": tolerance,
            "parent_comparison_id": parent,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def components_of(client, h, cid, asset):
    r = client.get(f"/v1/comparisons/{cid}/assets/{asset}", headers=h)
    assert r.status_code == 200, r.text
    return {c["component_id"]: c for c in r.json()["components"]}


def decide(client, h, cid, items):
    return client.post(f"/v1/comparisons/{cid}/decisions", headers=h, json={"decisions": items})


# --------------------------------------------------------------------------- auth & workspace


def test_requires_token_and_rejects_unknown(client):
    assert client.get("/v1/workspace").status_code == 401
    r = client.get("/v1/workspace", headers={"Authorization": "Bearer gdw_nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "workspace_expired"


def test_token_is_never_stored_in_plaintext(client):
    _, body = new_ws(client)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        row = conn.execute(
            "SELECT token_hash FROM workspaces WHERE id = %s", (body["workspace_id"],)
        ).fetchone()
    assert row is not None and body["token"].encode() not in bytes(row[0])
    assert len(bytes(row[0])) == 32


def test_cross_workspace_access_is_denied(client):
    h1, _ = new_ws(client)
    h2, _ = new_ws(client)
    run = golden_run(client, h1)
    cid = run["comparison_id"]
    ds = run["inputs"]["baseline"]["dataset_id"]
    for path in [
        f"/v1/datasets/{ds}",
        f"/v1/datasets/{ds}/original",
        f"/v1/comparisons/{cid}",
        f"/v1/comparisons/{cid}/queue",
        f"/v1/comparisons/{cid}/assets/BENCH-0142",
        f"/v1/comparisons/{cid}/export",
        f"/v1/comparisons/{cid}/history",
        f"/v1/comparisons/{cid}/map?bbox=-180,-90,180,90",
    ]:
        r = client.get(path, headers=h2)
        assert r.status_code == 404, path
    r = decide(
        client,
        h2,
        cid,
        [{"component_id": "BENCH-0142:move", "disposition": "accepted", "expected_version": 0}],
    )
    assert r.status_code == 404
    assert client.get("/v1/datasets", headers=h2).json()["datasets"] == []


def test_expired_workspace_is_refused_and_purged(client):
    h, body = new_ws(client)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE workspaces SET expires_at = now() - interval '1 second' WHERE id = %s",
            (body["workspace_id"],),
        )
    r = client.get("/v1/workspace", headers=h)
    assert r.status_code == 401 and r.json()["error"]["code"] == "workspace_expired"
    new_ws(client)  # creation purges expired workspaces
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        gone = conn.execute(
            "SELECT 1 FROM workspaces WHERE id = %s", (body["workspace_id"],)
        ).fetchone()
    assert gone is None


def test_delete_and_reset_workspace(client):
    h, _ = new_ws(client)
    golden_run(client, h)
    r = client.post("/v1/workspace/reset", headers=h)
    assert r.status_code == 200 and r.json()["datasets"] == 0 and r.json()["comparisons"] == 0
    assert client.delete("/v1/workspace", headers=h).status_code == 204
    assert client.get("/v1/workspace", headers=h).status_code == 401


# --------------------------------------------------------------------------- datasets


def test_upload_summary_duplicate_and_original(client):
    h, _ = new_ws(client)
    r = upload(client, h, GOLDEN / "baseline.geojson")
    assert r.status_code == 201
    d = r.json()
    assert d["valid"] and d["feature_count"] == 13 and d["duplicate"] is False
    assert "asset_id" in d["summary"]["candidate_id_fields"]
    again = upload(client, h, GOLDEN / "baseline.geojson", name="renamed.geojson")
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert again.json()["dataset_id"] == d["dataset_id"]
    orig = client.get(f"/v1/datasets/{d['dataset_id']}/original", headers=h)
    assert orig.content == (GOLDEN / "baseline.geojson").read_bytes()
    assert orig.headers["x-content-sha256"] == d["sha256"]


def test_invalid_upload_is_summarized_and_not_comparable(client):
    h, _ = new_ws(client)
    bad = upload(client, h, FIXTURES / "invalid" / "coordinate_out_of_range.geojson").json()
    assert bad["valid"] is False
    sample = bad["summary"]["validation"]["sample"][0]
    assert sample["code"] == "coordinate_out_of_range" and "EPSG:4326" in sample["guidance"]
    good = upload(client, h, GOLDEN / "baseline.geojson").json()
    r = client.post(
        "/v1/comparisons",
        headers=h,
        json={
            "baseline_dataset_id": good["dataset_id"],
            "proposed_dataset_id": bad["dataset_id"],
            "baseline_id_field": "asset_id",
        },
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "dataset_invalid"


def test_upload_limits_and_media_type(client):
    h, _ = new_ws(client)
    big = b"x" * (5 * 1024 * 1024 + 1)
    r = client.post("/v1/datasets", content=big, headers=h)
    assert r.status_code == 413 and r.json()["error"]["code"] == "upload_too_large"
    r = client.post(
        "/v1/datasets",
        content=b"<kml/>",
        headers=h | {"Content-Type": "application/vnd.google-earth.kml+xml"},
    )
    assert r.status_code == 415


def test_bad_id_field_is_explained(client):
    h, _ = new_ws(client)
    b = upload(client, h, GOLDEN / "baseline.geojson").json()
    r = client.post(
        "/v1/comparisons",
        headers=h,
        json={
            "baseline_dataset_id": b["dataset_id"],
            "proposed_dataset_id": b["dataset_id"],
            "baseline_id_field": "condition",
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "comparison_invalid"
    assert "duplicate_id" in r.json()["error"]["validation"]["counts"]


# --------------------------------------------------------------------------- review


def test_comparison_queue_map_and_evidence(client):
    h, _ = new_ws(client)
    run = golden_run(client, h)
    cid = run["comparison_id"]
    assert run["summary"]["components"] == 9
    assert run["progress"]["by_disposition"]["pending"] == 9

    seen, cursor = [], None
    while True:
        params = {"limit": 3} | ({"cursor": cursor} if cursor else {})
        page = client.get(f"/v1/comparisons/{cid}/queue", headers=h, params=params).json()
        assert page["total_assets"] == 8
        seen += [i["asset_id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == sorted(seen) and len(seen) == 8

    moves = client.get(f"/v1/comparisons/{cid}/queue", headers=h, params={"kind": "move"}).json()
    assert [i["asset_id"] for i in moves["items"]] == ["BENCH-0142", "TREE-0310"]

    near = client.get(
        f"/v1/comparisons/{cid}/map",
        headers=h,
        params={"bbox": "-96.7975,32.7850,-96.7960,32.7860"},
    ).json()
    assert [f["id"] for f in near["features"]] == ["BENCH-0142"]  # found by its proposed position
    base = client.get(
        f"/v1/comparisons/{cid}/baseline", headers=h, params={"bbox": "-96.80,32.78,-96.79,32.79"}
    ).json()
    assert len(base["features"]) == 13

    detail = client.get(f"/v1/comparisons/{cid}/assets/BENCH-0142", headers=h).json()
    kinds = [c["kind"] for c in detail["components"]]
    assert kinds == ["move", "attribute"]
    assert detail["components"][0]["evidence"]["distance_m"] == pytest.approx(119.6067, abs=1e-3)
    assert detail["baseline"]["properties"]["condition"] == "good"


def test_partial_decision_accepts_attribute_and_keeps_location(client):
    h, _ = new_ws(client)
    cid = golden_run(client, h)["comparison_id"]
    r = decide(
        client,
        h,
        cid,
        [
            {
                "component_id": "BENCH-0142:attr:condition",
                "disposition": "accepted",
                "expected_version": 0,
            },
            {
                "component_id": "BENCH-0142:move",
                "disposition": "rejected",
                "note": "Across 11th St",
                "expected_version": 0,
            },
        ],
    )
    assert r.status_code == 200, r.text
    fc = client.get(f"/v1/comparisons/{cid}/export", headers=h, params={"part": "geojson"}).json()
    bench = next(f for f in fc["features"] if f["properties"]["asset_id"] == "BENCH-0142")
    assert bench["geometry"]["coordinates"] == [-96.7969, 32.78434]
    assert bench["properties"]["condition"] == "damaged"


def test_stale_tab_cannot_overwrite_newer_decision(client):
    h, _ = new_ws(client)
    cid = golden_run(client, h)["comparison_id"]
    v = components_of(client, h, cid, "TREE-0310")["TREE-0310:move"]["decision"]["version"]
    tab_a = decide(
        client,
        h,
        cid,
        [{"component_id": "TREE-0310:move", "disposition": "accepted", "expected_version": v}],
    )
    assert tab_a.status_code == 200
    tab_b = decide(
        client,
        h,
        cid,
        [
            {
                "component_id": "TABLE-0003:attr:material",
                "disposition": "accepted",
                "expected_version": 0,
            },
            {"component_id": "TREE-0310:move", "disposition": "rejected", "expected_version": v},
        ],
    )
    assert tab_b.status_code == 409 and tab_b.json()["error"]["code"] == "stale_decision"
    assert (
        components_of(client, h, cid, "TREE-0310")["TREE-0310:move"]["decision"]["disposition"]
        == "accepted"
    )
    # Atomic: the other item in the stale request was not applied either.
    assert (
        components_of(client, h, cid, "TABLE-0003")["TABLE-0003:attr:material"]["decision"][
            "disposition"
        ]
        == "pending"
    )


def test_concurrent_identical_decisions_one_wins(client):
    h, _ = new_ws(client)
    cid = golden_run(client, h)["comparison_id"]
    item = {"component_id": "FOUNT-0009:deletion", "disposition": "accepted", "expected_version": 0}
    with ThreadPoolExecutor(4) as pool:
        codes = sorted(pool.map(lambda _: decide(client, h, cid, [item]).status_code, range(4)))
    assert codes.count(200) == 1 and codes.count(409) == 3


def test_flag_needs_note_and_stays_unresolved(client):
    h, _ = new_ws(client)
    cid = golden_run(client, h)["comparison_id"]
    r = decide(
        client,
        h,
        cid,
        [
            {
                "component_id": "BIKE-0012:attr:install_year",
                "disposition": "flagged",
                "expected_version": 0,
            }
        ],
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "note_required"
    r = decide(
        client,
        h,
        cid,
        [
            {
                "component_id": "BIKE-0012:attr:install_year",
                "disposition": "flagged",
                "note": "Ask crew",
                "expected_version": 0,
            }
        ],
    )
    assert r.json()["progress"]["by_disposition"]["flagged"] == 1
    assert r.json()["progress"]["complete"] is False


def test_bulk_requires_matching_count_and_scope(client):
    h, _ = new_ws(client)
    cid = golden_run(client, h)["comparison_id"]
    scope = {"kind": "attribute", "current_disposition": "pending"}
    prev = client.post(f"/v1/comparisons/{cid}/bulk/preview", headers=h, json=scope).json()
    assert prev["count"] == 5
    r = client.post(
        f"/v1/comparisons/{cid}/bulk",
        headers=h,
        json={"scope": scope, "disposition": "accepted", "expected_count": 4},
    )
    assert r.status_code == 409 and r.json()["error"]["actual_count"] == 5
    r = client.post(
        f"/v1/comparisons/{cid}/bulk",
        headers=h,
        json={"scope": {}, "disposition": "accepted", "expected_count": 9},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "scope_required"
    r = client.post(
        f"/v1/comparisons/{cid}/bulk",
        headers=h,
        json={"scope": scope, "disposition": "accepted", "expected_count": 5},
    )
    assert r.status_code == 200 and r.json()["applied"] == 5


def test_change_of_mind_and_undo_are_audited(client):
    h, _ = new_ws(client)
    cid = golden_run(client, h)["comparison_id"]
    decide(
        client,
        h,
        cid,
        [{"component_id": "TREE-0310:move", "disposition": "accepted", "expected_version": 0}],
    )
    r = decide(
        client,
        h,
        cid,
        [{"component_id": "TREE-0310:move", "disposition": "rejected", "expected_version": 1}],
    )
    rev = r.json()["revision"]
    stale = client.post(
        f"/v1/comparisons/{cid}/undo", headers=h, json={"expected_revision": rev - 1}
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_revision"
    u = client.post(f"/v1/comparisons/{cid}/undo", headers=h, json={"expected_revision": rev})
    assert u.status_code == 200 and u.json()["components"] == ["TREE-0310:move"]
    assert (
        components_of(client, h, cid, "TREE-0310")["TREE-0310:move"]["decision"]["disposition"]
        == "accepted"
    )
    hist = client.get(f"/v1/comparisons/{cid}/history", headers=h).json()["events"]
    assert [e["action"] for e in hist] == ["undo", "decide", "decide"]
    assert [e["undone"] for e in hist] == [False, True, False]


def test_final_export_blocked_then_golden_round_trip(client):
    h, _ = new_ws(client)
    cid = golden_run(client, h)["comparison_id"]
    r = client.get(f"/v1/comparisons/{cid}/export", headers=h, params={"mode": "final"})
    assert r.status_code == 409 and r.json()["error"]["unresolved_count"] == 9
    draft = client.get(f"/v1/comparisons/{cid}/export", headers=h, params={"mode": "draft"}).json()
    assert draft["report"]["counts"]["unresolved_components"] == 9
    assert any("BENCH-0901" in n for n in draft["report"]["notes"])

    golden = json.loads((GOLDEN / "decisions.json").read_text())
    items = [
        {
            "component_id": k,
            "disposition": v["disposition"],
            "note": v.get("note"),
            "expected_version": 0,
        }
        for k, v in golden.items()
    ]
    assert decide(client, h, cid, items).status_code == 200
    fc = client.get(
        f"/v1/comparisons/{cid}/export", headers=h, params={"mode": "final", "part": "geojson"}
    )
    assert fc.status_code == 200
    assert fc.json() == json.loads((GOLDEN / "expected_reviewed.geojson").read_text())
    report = client.get(
        f"/v1/comparisons/{cid}/export", headers=h, params={"mode": "final", "part": "report"}
    ).json()
    assert report["complete"] and report["parameters"]["tolerance_m"] == 2.0
    assert len(report["history"]) == 9

    # Re-import the export as a new dataset in the same workspace.
    re = client.post(
        "/v1/datasets", params={"name": "reviewed.geojson"}, content=fc.content, headers=h
    )
    assert re.status_code == 201 and re.json()["valid"] and re.json()["feature_count"] == 13


def test_new_run_keeps_lineage_and_old_decisions(client):
    h, _ = new_ws(client)
    first = golden_run(client, h)
    decide(
        client,
        h,
        first["comparison_id"],
        [{"component_id": "TREE-0310:move", "disposition": "accepted", "expected_version": 0}],
    )
    second = golden_run(client, h, tolerance=150.0, parent=first["comparison_id"])
    assert second["parent_comparison_id"] == first["comparison_id"]
    assert second["summary"]["components_by_kind"]["move"] == 0
    old = client.get(f"/v1/comparisons/{first['comparison_id']}", headers=h).json()
    assert old["progress"]["by_disposition"]["accepted"] == 1
    assert len(client.get("/v1/comparisons", headers=h).json()["comparisons"]) == 2


def test_state_survives_pool_restart(client):
    h, _ = new_ws(client)
    cid = golden_run(client, h)["comparison_id"]
    decide(
        client,
        h,
        cid,
        [{"component_id": "TREE-0310:move", "disposition": "accepted", "expected_version": 0}],
    )
    close_pool()
    with TestClient(app) as fresh:
        r = fresh.get(f"/v1/comparisons/{cid}", headers=h).json()
    assert r["progress"]["by_disposition"]["accepted"] == 1


def test_openapi_documents_the_contract(client):
    spec = client.get("/openapi.json").json()
    for path in [
        "/v1/workspaces",
        "/v1/datasets",
        "/v1/comparisons",
        "/v1/comparisons/{cid}/queue",
        "/v1/comparisons/{cid}/map",
        "/v1/comparisons/{cid}/assets/{asset_id}",
        "/v1/comparisons/{cid}/decisions",
        "/v1/comparisons/{cid}/history",
        "/v1/comparisons/{cid}/export",
    ]:
        assert path in spec["paths"], path


def test_errors_never_leak_internals(client):
    h, _ = new_ws(client)
    r = client.get("/v1/comparisons/not-a-uuid", headers=h)
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "invalid_request" and "Traceback" not in r.text


def test_suggest_fields_and_extent(client):
    h, _ = new_ws(client)
    run = golden_run(client, h)
    b = run["inputs"]["baseline"]["dataset_id"]
    p = run["inputs"]["proposed"]["dataset_id"]
    r = client.get(
        "/v1/datasets/suggest-fields",
        headers=h,
        params={"baseline": b, "proposed": p, "baseline_id_field": "asset_id"},
    ).json()
    assert {"condition", "material", "notes", "install_year"} <= set(r["suggested"])
    notes = {f["name"]: f["note"] for f in r["fields"]}
    assert notes["imported_at"] == "importer/editor bookkeeping"
    assert notes["maintenance"].startswith("nested")
    assert notes["asset_id"] == "ID field"
    x0, y0, x1, y1 = run["extent"]
    assert x0 <= -96.7969 <= x1 and y0 <= 32.785412 <= y1
