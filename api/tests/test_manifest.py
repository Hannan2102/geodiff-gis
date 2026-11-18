"""The engine against the generator's independent change manifest (5,000 synthetic assets)."""

import hashlib
import json

import pytest

from geodiff import synth
from geodiff.engine.compare import CompareOptions, compare_datasets
from tests.helpers import from_obj


@pytest.fixture(scope="module")
def run():
    base, prop, manifest = synth.generate()
    opts = CompareOptions("asset_id", "asset_id", synth.DEFAULT_COMPARE_FIELDS, synth.TOLERANCE_M)
    result = compare_datasets(from_obj(base), from_obj(prop), opts)
    return base, prop, manifest, result


def test_component_ids_match_manifest_exactly(run):
    _, _, manifest, result = run
    got = {c["id"] for a in result["assets"] for c in a["components"]}
    want = {c["id"] for a in manifest["assets"] for c in a["components"]}
    assert got - want == set(), "false positives"
    assert want - got == set(), "missed changes"
    assert result["summary"]["components_by_kind"] == manifest["counts"]["components_by_kind"]
    assert result["summary"]["affected_assets"] == manifest["counts"]["affected_assets"]


def test_within_tolerance_and_near_threshold(run):
    _, _, manifest, result = run
    assert (
        result["summary"]["within_tolerance_moves"] == manifest["counts"]["within_tolerance_moves"]
    )
    moves = {c["id"]: c for a in result["assets"] for c in a["components"] if c["kind"] == "move"}
    near = [c for a in manifest["assets"] for c in a["components"] if c.get("near_threshold")]
    assert len(near) == 10
    for c in near:
        assert 2.0 < moves[c["id"]]["evidence"]["distance_m"] < 2.06


def test_move_distances_match_intent(run):
    _, _, manifest, result = run
    moves = {c["id"]: c for a in result["assets"] for c in a["components"] if c["kind"] == "move"}
    checked = 0
    for a in manifest["assets"]:
        for c in a["components"]:
            if c["kind"] == "move" and "intended_distance_m" in c:
                assert moves[c["id"]]["evidence"]["distance_m"] == pytest.approx(
                    c["intended_distance_m"], abs=0.005
                )
                checked += 1
    assert checked == manifest["counts"]["components_by_kind"]["move"] - 1  # minus BENCH-0142


def test_attribute_values_match_manifest(run):
    _, _, manifest, result = run
    got = {c["id"]: c for a in result["assets"] for c in a["components"]}
    for a in manifest["assets"]:
        for c in a["components"]:
            if c["kind"] == "attribute" and "new" in c and c.get("new_present", True):
                assert got[c["id"]]["evidence"]["new"] == c["new"]


def test_named_bench_in_synthetic_set(run):
    _, _, _, result = run
    bench = next(a for a in result["assets"] if a["asset_id"] == "BENCH-0142")
    kinds = sorted(c["id"] for c in bench["components"])
    assert kinds == ["BENCH-0142:attr:condition", "BENCH-0142:move"]
    assert bench["components"][0]["evidence"]["distance_m"] == pytest.approx(119.6067, abs=1e-3)


def test_unchanged_controls_and_importer_timestamps_are_quiet(run):
    _, _, manifest, result = run
    affected = {a["asset_id"] for a in result["assets"]}
    assert not affected & set(manifest["unchanged_controls"])
    assert all(c.get("field") != "imported_at" for a in result["assets"] for c in a["components"])


def test_generator_is_deterministic():
    a = synth.generate()
    b = synth.generate()
    h = [hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest() for x in (a, b)]
    assert h[0] == h[1]
    assert synth.generate(seed=1)[0] != a[0]
