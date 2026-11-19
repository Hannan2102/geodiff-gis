"""The committed guided-sample bundle must be exactly what the engine produces today."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from geodiff import sample

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "fixtures" / "demo"
GOLDEN = ROOT / "fixtures" / "golden"


def _same(a: Any, b: Any, path: str = "$") -> None:
    """Exact structure, IDs and strings; floats near-exact (geodesic fields to 1e-6).

    Geodesic results differ in the last ulp between platforms (e.g. macOS arm64 vs Linux
    x86_64 libm); bundle hashes inherit that, so they are not compared.
    """
    if isinstance(a, dict):
        assert isinstance(b, dict) and a.keys() == b.keys(), f"{path}: keys differ"
        for k in a:
            if k != "bundle_sha256":
                _same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), f"{path}: lengths differ"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _same(x, y, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        # Computed geodesics may differ by nanometres between platforms (FMA, libm);
        # 1 micrometre / 1 micro-degree is far below any classification margin (>= 1 cm).
        # Everything else (coordinates are copied from inputs) must match to 1e-12.
        geodesic = path.endswith((".distance_m", ".bearing_deg"))
        tol = 1e-6 if geodesic else 1e-12
        assert math.isclose(a, b, rel_tol=1e-12, abs_tol=tol), f"{path}: {a} != {b}"
    else:
        assert type(a) is type(b) and a == b, f"{path}: {a!r} != {b!r}"


def test_committed_bundle_is_current(tmp_path: Path) -> None:
    out = tmp_path / "demo"
    (out / "inputs").mkdir(parents=True)
    for f in (DEMO / "inputs").iterdir():
        (out / "inputs" / f.name).write_bytes(f.read_bytes())
    paths = sample.build_all(out, GOLDEN)
    for name, rel in (
        ("sample", "sample.json"),
        ("parity", "parity/sample_parity.json"),
        ("golden", "parity/golden_bundle.json"),
    ):
        fresh = json.loads(paths[name].read_text())
        committed = json.loads((DEMO / rel).read_text())
        _same(fresh, committed, rel)  # stale? run `make sample` and commit the result


def test_bundle_story_and_presets() -> None:
    bundle = json.loads((DEMO / "sample.json").read_text())
    assert bundle["schema"] == "geodiff.sample/1"
    assert bundle["synthetic"] is True
    assert "SYNTHETIC" in bundle["attribution"]["note"]
    assert [p["tolerance_m"] for p in bundle["presets"]] == [1.0, 2.0, 5.0]
    two = next(p for p in bundle["presets"] if p["tolerance_m"] == 2.0)["comparison"]
    bench = next(a for a in two["assets"] if a["asset_id"] == "BENCH-0142")
    assert [c["id"] for c in bench["components"]] == [
        "BENCH-0142:move",
        "BENCH-0142:attr:condition",
    ]
    assert round(bench["components"][0]["evidence"]["distance_m"], 4) == 119.6067
    # Larger tolerances can only reduce move components, never other kinds.
    kinds = [p["comparison"]["summary"]["components_by_kind"] for p in bundle["presets"]]
    assert kinds[0]["move"] >= kinds[1]["move"] >= kinds[2]["move"]
    assert len({(k["addition"], k["deletion"], k["attribute"]) for k in kinds}) == 1


def test_sample_matches_generator_manifest() -> None:
    """At the generator's 2 m tolerance, detected changes equal the injected ground truth."""
    manifest = json.loads((DEMO / "inputs" / "manifest.json").read_text())
    bundle = json.loads((DEMO / "sample.json").read_text())
    two = next(p for p in bundle["presets"] if p["tolerance_m"] == 2.0)["comparison"]
    detected = sorted(c["id"] for a in two["assets"] for c in a["components"])
    expected = sorted(c["id"] for a in manifest["assets"] for c in a["components"])
    assert detected == expected
    assert two["summary"]["components_by_kind"] == manifest["counts"]["components_by_kind"]
    assert two["summary"]["within_tolerance_moves"] == manifest["counts"]["within_tolerance_moves"]
