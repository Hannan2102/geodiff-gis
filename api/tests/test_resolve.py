import copy
import json

import pytest

from geodiff.engine.compare import CompareOptions, baseline_snapshot, compare_datasets
from geodiff.engine.resolve import FinalExportBlocked, InvalidDecision, resolve
from tests.helpers import GOLDEN, GOLDEN_FIELDS, fc, from_obj, golden_comparison, pt


def golden_decisions():
    return json.loads((GOLDEN / "decisions.json").read_text())


def golden_resolve(decisions, mode="draft"):
    base, comparison = golden_comparison()
    snap = baseline_snapshot(base, "asset_id")
    return resolve(snap, comparison, decisions, mode=mode, exported_at="2026-11-18T16:00:00+00:00")


def by_id(fc_):
    return {f["properties"]["asset_id"]: f for f in fc_["features"]}


def test_golden_final_export_matches_hand_authored_result():
    out, report = golden_resolve(golden_decisions(), mode="final")
    expected = json.loads((GOLDEN / "expected_reviewed.geojson").read_text())
    assert out == expected
    assert report["complete"] is True
    assert report["counts"]["by_disposition"] == {
        "pending": 0,
        "accepted": 6,
        "rejected": 3,
        "flagged": 0,
    }


def test_partial_feature_decision_keeps_location_and_applies_attribute():
    out, _ = golden_resolve(golden_decisions())
    bench = by_id(out)["BENCH-0142"]
    assert bench["geometry"]["coordinates"] == [-96.7969, 32.78434]
    assert bench["properties"]["condition"] == "damaged"
    assert bench["properties"]["maintenance"] == {"crew": "Central", "contract": "PR-2025-07"}


def test_final_export_blocked_until_all_resolved_and_draft_explains():
    decisions = golden_decisions()
    del decisions["BENCH-0901:addition"]
    decisions["BIKE-0012:attr:install_year"] = {"disposition": "flagged", "note": "Ask crew"}
    with pytest.raises(FinalExportBlocked) as exc:
        golden_resolve(decisions, mode="final")
    assert {u["component_id"] for u in exc.value.unresolved} == {
        "BENCH-0901:addition",
        "BIKE-0012:attr:install_year",
    }
    out, report = golden_resolve(decisions)
    assert "BENCH-0901" not in by_id(out)
    assert any("BENCH-0901" in n and "omitted" in n for n in report["notes"])
    assert report["counts"]["unresolved_components"] == 2
    assert report["complete"] is False
    assert by_id(out)["BIKE-0012"]["properties"]["install_year"] == 2019


def test_flag_requires_note():
    with pytest.raises(InvalidDecision):
        golden_resolve({"TREE-0310:move": {"disposition": "flagged"}})


def test_unknown_component_rejected():
    with pytest.raises(InvalidDecision):
        golden_resolve({"NOPE:move": {"disposition": "accepted"}})


@pytest.mark.parametrize(
    ("disposition", "bench_present", "fount_present"),
    [("accepted", True, False), ("rejected", False, True), ("pending", False, True)],
)
def test_addition_and_deletion_membership(disposition, bench_present, fount_present):
    d = {
        "BENCH-0901:addition": {"disposition": disposition},
        "FOUNT-0009:deletion": {"disposition": disposition},
    }
    out, _ = golden_resolve(d)
    ids = by_id(out)
    assert ("BENCH-0901" in ids) is bench_present
    assert ("FOUNT-0009" in ids) is fount_present


def test_report_contains_lineage_parameters_and_hashes():
    _, report = golden_resolve(golden_decisions())
    assert report["schema"] == "geodiff.report/1"
    assert report["parameters"]["tolerance_m"] == 2.0
    assert report["parameters"]["units"] == "meters"
    assert report["parameters"]["compared_fields"] == list(GOLDEN_FIELDS)
    assert len(report["inputs"]["baseline"]["sha256"]) == 64
    note = next(r for r in report["decisions"] if r["component_id"] == "BENCH-0142:move")
    assert note["disposition"] == "rejected" and "11th St" in note["note"]


def test_export_reimports_and_recompares_cleanly():
    out, _ = golden_resolve(golden_decisions(), mode="final")
    reimported = from_obj(out, "reviewed.geojson")
    assert reimported.valid and reimported.feature_count == 13
    expected = from_obj(json.loads((GOLDEN / "expected_reviewed.geojson").read_text()))
    again = compare_datasets(
        expected, reimported, CompareOptions("asset_id", "asset_id", GOLDEN_FIELDS, 0.0)
    )
    assert again["assets"] == []


def test_resolution_does_not_mutate_inputs():
    base, comparison = golden_comparison()
    snap = baseline_snapshot(base, "asset_id")
    before = (copy.deepcopy(snap), copy.deepcopy(comparison))
    resolve(snap, comparison, {k: {"disposition": "accepted"} for k in golden_decisions()})
    assert (snap, comparison) == before


def test_sub_threshold_motion_keeps_baseline_position():
    base = from_obj(fc(pt(-96.8, 32.78, "A")))
    prop = from_obj(fc(pt(-96.8, 32.780005, "A")))  # ~0.55 m
    comp = compare_datasets(base, prop, CompareOptions("asset_id", "asset_id", (), 2.0))
    out, _ = resolve(baseline_snapshot(base, "asset_id"), comp, {})
    assert out["features"][0]["geometry"]["coordinates"] == [-96.8, 32.78]
