"""Build the static guided-sample bundle with the real comparison engine.

The guided sample runs entirely in the browser. It ships the normalized baseline snapshot and
the engine's serialized comparison (evidence + patches) for a few exact tolerance presets; the
browser only applies patches for the reviewer's decisions (see ``web/src/adapters/sample.ts``).

Inputs are committed (``fixtures/demo/inputs``) so rebuilding never depends on the generator's
floating-point behaviour on a given platform. ``make sample`` regenerates everything;
``tests/test_sample.py`` fails if the committed bundle is stale.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from geodiff import __version__
from geodiff.engine.compare import CompareOptions, baseline_snapshot, compare_datasets
from geodiff.engine.geojson import parse_geojson
from geodiff.engine.resolve import resolve
from geodiff.synth import DEFAULT_COMPARE_FIELDS, SYNTHETIC_NOTE, Counts, generate

SAMPLE_SCHEMA = "geodiff.sample/1"
PRESETS_M = (1.0, 2.0, 5.0)
DEFAULT_PRESET_M = 2.0
SAMPLE_SEED = 20261118
CREATED_AT = "2026-11-18T12:00:00+00:00"
EXPORTED_AT = "2026-11-18T12:30:00+00:00"
ID_FIELD = "asset_id"
FOCUS_ASSET = "BENCH-0142"

SAMPLE_COUNTS = Counts(
    n=400,
    deletions=6,
    additions=7,
    moves=10,
    move_and_edit=3,
    edits=18,
    jitter=70,
    near_below=3,
    near_above=3,
    id_changes=1,
    null_to_absent=1,
    absent_to_null=1,
    number_to_string=1,
)

ATTRIBUTION = {
    "source": "GeoDiff synthetic generator (seeded)",
    "owner": "Cedar Hollow Parks Department (fictional), a synthetic Dallas-area grid",
    "note": SYNTHETIC_NOTE,
    "license": "MIT, same as GeoDiff",
}


def _dump(obj: Any, path: Path, *, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    else:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_text(text, encoding="utf-8")


def write_inputs(demo_dir: Path) -> None:
    """(Re)generate the committed sample inputs from the seeded generator."""
    base, prop, manifest = generate(SAMPLE_COUNTS, seed=SAMPLE_SEED)
    inputs = demo_dir / "inputs"
    _dump(base, inputs / "cedar-hollow-approved-2026-10.geojson", pretty=False)
    _dump(prop, inputs / "cedar-hollow-field-2026-11.geojson", pretty=False)
    _dump(manifest, inputs / "manifest.json", pretty=True)


def build_bundle(
    baseline_path: Path,
    proposed_path: Path,
    *,
    compare_fields: tuple[str, ...],
    presets: tuple[float, ...] = PRESETS_M,
    default_preset: float = DEFAULT_PRESET_M,
    focus_asset: str | None = FOCUS_ASSET,
    title: str,
    attribution: dict[str, Any] = ATTRIBUTION,
) -> dict[str, Any]:
    base = parse_geojson(baseline_path.read_bytes(), name=baseline_path.name)
    prop = parse_geojson(proposed_path.read_bytes(), name=proposed_path.name)
    runs = []
    for tol in presets:
        opts = CompareOptions(ID_FIELD, ID_FIELD, compare_fields, tol)
        runs.append(
            {
                "tolerance_m": tol,
                "comparison": compare_datasets(base, prop, opts, created_at=CREATED_AT),
            }
        )
    bundle = {
        "schema": SAMPLE_SCHEMA,
        "title": title,
        "synthetic": True,
        "engine_version": __version__,
        "generated_by": "geodiff sample (api/geodiff/sample.py) with the Python comparison engine",
        "attribution": attribution,
        "focus_asset": focus_asset,
        "default_tolerance_m": default_preset,
        "baseline_snapshot": baseline_snapshot(base, ID_FIELD),
        "presets": runs,
    }
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    bundle["bundle_sha256"] = hashlib.sha256(canonical).hexdigest()
    return bundle


def parity_decisions(comparison: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A deterministic mix of every disposition, plus the BENCH-0142 story decisions."""
    out: dict[str, dict[str, Any]] = {}
    cycle = ("accepted", "rejected", "pending", "flagged", "accepted")
    i = 0
    for asset in comparison["assets"]:
        for comp in asset["components"]:
            disp = cycle[i % len(cycle)]
            i += 1
            if comp["id"] == "BENCH-0142:attr:condition":
                disp = "accepted"
            elif comp["id"] == "BENCH-0142:move":
                disp = "rejected"
            if disp == "pending":
                continue
            d: dict[str, Any] = {"disposition": disp, "decided_at": EXPORTED_AT}
            if disp == "flagged":
                d["note"] = f"Check {asset['asset_id']} with the field crew."
            out[comp["id"]] = d
    return out


def build_parity(bundle: dict[str, Any]) -> dict[str, Any]:
    """Python reference exports for the browser resolver to match (draft, every preset)."""
    cases = []
    for run in bundle["presets"]:
        comparison = run["comparison"]
        decisions = parity_decisions(comparison)
        fc, report = resolve(
            bundle["baseline_snapshot"],
            comparison,
            decisions,
            mode="draft",
            exported_at=EXPORTED_AT,
            attribution=bundle["attribution"],
        )
        cases.append(
            {
                "tolerance_m": run["tolerance_m"],
                "decisions": decisions,
                "geojson": fc,
                "report": report,
            }
        )
    return {
        "schema": "geodiff.sample-parity/1",
        "bundle_sha256": bundle["bundle_sha256"],
        "cases": cases,
    }


def build_all(demo_dir: Path, golden_dir: Path) -> dict[str, Path]:
    inputs = demo_dir / "inputs"
    sample = build_bundle(
        inputs / "cedar-hollow-approved-2026-10.geojson",
        inputs / "cedar-hollow-field-2026-11.geojson",
        compare_fields=DEFAULT_COMPARE_FIELDS,
        title="Cedar Hollow parks inventory: approved October vs. field survey November (synthetic)",
    )
    golden = build_bundle(
        golden_dir / "baseline.geojson",
        golden_dir / "proposed.geojson",
        compare_fields=("condition", "install_year", "material", "notes"),
        presets=(2.0,),
        title="Golden fixture (hand-authored)",
        attribution={"note": "Hand-authored golden fixture; synthetic."},
    )
    paths = {
        "sample": demo_dir / "sample.json",
        "parity": demo_dir / "parity" / "sample_parity.json",
        "golden": demo_dir / "parity" / "golden_bundle.json",
    }
    _dump(sample, paths["sample"])
    _dump(build_parity(sample), paths["parity"])
    _dump(golden, paths["golden"])
    return paths
