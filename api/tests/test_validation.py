import pytest

from geodiff.engine.geojson import parse_geojson
from geodiff.engine.ids import index_ids
from tests.helpers import FIXTURES, GOLDEN, fc, from_obj, load, pt

INVALID = FIXTURES / "invalid"
ID_CODES = {"duplicate_id", "ambiguous_id", "null_id", "empty_id"}


@pytest.mark.parametrize("path", sorted(INVALID.glob("*.geojson")), ids=lambda p: p.stem)
def test_each_invalid_fixture_reports_its_code(path):
    ds = load(path)
    code = path.stem
    if code in ID_CODES:
        assert ds.valid, ds.issues.to_dict()
        _, log = index_ids(ds.features, "asset_id")
        assert code in log.counts
    else:
        assert not ds.valid
        assert code in ds.issues.counts, ds.issues.to_dict()
        issue = next(i for i in ds.issues.samples if i.code == code).to_dict()
        assert issue["guidance"], "every error carries actionable guidance"


def test_golden_files_are_valid_and_summarized():
    ds = load(GOLDEN / "baseline.geojson")
    s = ds.summary()
    assert s["valid"] and s["feature_count"] == 13
    assert len(s["sha256"]) == 64
    assert "asset_id" in s["candidate_id_fields"]
    assert "condition" not in s["candidate_id_fields"]
    fields = {f["name"]: f for f in s["fields"]}
    assert fields["notes"]["present"] == 4  # absent on the rest
    assert fields["maintenance"]["scalar"] is False


def test_out_of_range_message_mentions_reprojection():
    ds = load(INVALID / "coordinate_out_of_range.geojson")
    issue = ds.issues.samples[0].to_dict()
    assert "EPSG:4326" in issue["guidance"]


def test_wgs84_legacy_crs_is_a_warning_not_an_error():
    doc = fc(pt(-96.8, 32.78, "A"))
    doc["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    ds = from_obj(doc)
    assert ds.valid
    assert ds.issues.counts["legacy_crs_member"] == 1


def test_issue_sampling_keeps_exact_counts():
    feats = [
        {"type": "Feature", "geometry": None, "properties": {"asset_id": i}} for i in range(50)
    ]
    ds = from_obj(fc(*feats))
    d = ds.issues.to_dict()
    assert d["counts"]["null_geometry"] == 50
    assert len(d["sample"]) == 20 and d["sampled"] is True


def test_infinity_in_properties_is_rejected():
    raw = b'{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[1,2]},"properties":{"asset_id":"A","h":1e999}}]}'
    ds = parse_geojson(raw)
    assert ds.issues.counts["nonfinite_value"] == 1


def test_feature_limit_is_enforced():
    ds = parse_geojson(
        b'{"type":"FeatureCollection","features":[' + b",".join([b"{}"] * 11) + b"]}",
        max_features=10,
    )
    assert "too_many_features" in ds.issues.counts


def test_bom_is_accepted():
    raw = "﻿" + '{"type":"FeatureCollection","features":[]}'
    assert parse_geojson(raw.encode("utf-8")).valid
