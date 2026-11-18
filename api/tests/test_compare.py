import json

import pytest

from geodiff.engine.compare import (
    CompareOptions,
    ComparisonError,
    compare_datasets,
    suggested_compare_fields,
    values_equal,
)
from tests.helpers import GOLDEN, fc, from_obj, golden_comparison, load, pt


def components(result):
    return {c["id"]: c for a in result["assets"] for c in a["components"]}


def test_golden_matches_hand_authored_expectation():
    _, r = golden_comparison()
    expected = json.loads((GOLDEN / "expected_changes.json").read_text())
    for key, value in expected["summary"].items():
        assert r["summary"][key] == value, key
    got = components(r)
    assert set(got) == {c["id"] for c in expected["components"]}
    for exp in expected["components"]:
        c = got[exp["id"]]
        assert c["kind"] == exp["kind"]
        if "patch" in exp:
            assert c["patch"] == exp["patch"], exp["id"]
        if "approx_distance_m" in exp:
            lo, hi = exp["approx_distance_m"]
            assert lo < c["evidence"]["distance_m"] < hi
        for k in ("old", "new", "old_present", "new_present"):
            if k in exp:
                assert c["evidence"][k] == exp[k], (exp["id"], k)


def test_named_bench_has_two_grouped_components_and_measured_distance():
    _, r = golden_comparison()
    bench = next(a for a in r["assets"] if a["asset_id"] == "BENCH-0142")
    assert [c["kind"] for c in bench["components"]] == ["move", "attribute"]
    move = bench["components"][0]["evidence"]
    assert move["distance_m"] == pytest.approx(119.6067, abs=1e-3)
    assert 0 < move["bearing_deg"] < 15  # roughly north


def test_assets_are_sorted_and_output_is_deterministic():
    _, a = golden_comparison()
    _, b = golden_comparison()
    assert a == b
    ids = [x["asset_id"] for x in a["assets"]]
    assert ids == sorted(ids)


def test_never_matches_by_position():
    base = from_obj(fc(pt(0, 0, "A", c="x"), pt(1, 1, "B", c="y")))
    prop = from_obj(fc(pt(1, 1, "B", c="y"), pt(0, 0, "A", c="x")))
    r = compare_datasets(base, prop, CompareOptions("asset_id", "asset_id", ("c",), 2.0))
    assert r["assets"] == []


def test_changed_id_is_an_addition_and_a_deletion():
    base = from_obj(fc(pt(0, 0, "A"), pt(1, 1, "B")))
    prop = from_obj(fc(pt(0, 0, "A2"), pt(1, 1, "B")))
    r = compare_datasets(base, prop, CompareOptions("asset_id", "asset_id", (), 2.0))
    assert set(components(r)) == {"A:deletion", "A2:addition"}


def test_low_overlap_and_leading_zero_hint():
    base = from_obj(fc(*[pt(0, 0, f"{i:04d}") for i in range(1, 11)]))
    prop = from_obj(fc(*[pt(0, 0, i) for i in range(1, 11)]))
    r = compare_datasets(base, prop, CompareOptions("asset_id", "asset_id", (), 2.0))
    codes = {w["code"] for w in r["warnings"]}
    assert {"low_id_overlap", "leading_zero_hint"} <= codes


def test_numeric_and_string_ids_match_with_a_warning():
    base = from_obj(fc(pt(0, 0, 142)))
    prop = from_obj(fc(pt(0, 0, "142")))
    r = compare_datasets(base, prop, CompareOptions("asset_id", "asset_id", (), 2.0))
    assert r["summary"]["matched_assets"] == 1
    assert any(w["code"] == "id_type_mismatch" for w in r["warnings"])


def test_different_id_fields_per_version_and_addition_rekeyed():
    base = from_obj(fc(pt(0, 0, "A")))
    prop_doc = fc(
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0, 0]},
            "properties": {"AssetID": "A"},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1, 1]},
            "properties": {"AssetID": "N", "x": 1},
        },
    )
    r = compare_datasets(base, from_obj(prop_doc), CompareOptions("asset_id", "AssetID", (), 2.0))
    add = components(r)["N:addition"]["patch"]["feature"]["properties"]
    assert add == {"x": 1, "asset_id": "N"}


@pytest.mark.parametrize(
    ("a", "b", "equal"),
    [
        ("5", 5, False),
        (5, 5.0, True),
        (True, 1, False),
        (False, 0, False),
        (None, None, True),
        ("", None, False),
    ],
)
def test_scalar_equality_keeps_json_types_distinct(a, b, equal):
    assert values_equal(a, b) is equal


def test_id_fields_and_nested_fields_cannot_be_compared():
    base = load(GOLDEN / "baseline.geojson")
    prop = load(GOLDEN / "proposed.geojson")
    with pytest.raises(ComparisonError) as exc:
        compare_datasets(
            base, prop, CompareOptions("asset_id", "asset_id", ("asset_id", "maintenance"), 2.0)
        )
    assert {"id_field_compared", "nested_compare_field"} <= set(exc.value.issues.counts)


def test_duplicate_ids_block_comparison():
    base = from_obj(fc(pt(0, 0, "A"), pt(1, 1, "A")))
    prop = from_obj(fc(pt(0, 0, "A")))
    with pytest.raises(ComparisonError) as exc:
        compare_datasets(base, prop, CompareOptions("asset_id", "asset_id", (), 2.0))
    assert "duplicate_id" in exc.value.issues.counts


def test_suggested_fields_exclude_ids_timestamps_and_nested():
    base = load(GOLDEN / "baseline.geojson")
    prop = load(GOLDEN / "proposed.geojson")
    fields = suggested_compare_fields(base, prop, {"asset_id"})
    assert "imported_at" not in fields
    assert "asset_id" not in fields
    assert "maintenance" not in fields
    assert {"condition", "material", "notes", "install_year"} <= set(fields)


def test_negative_tolerance_rejected():
    base = from_obj(fc(pt(0, 0, "A")))
    with pytest.raises(ComparisonError):
        compare_datasets(base, base, CompareOptions("asset_id", "asset_id", (), -1.0))
