"""Write one small invalid GeoJSON file per validation failure to fixtures/invalid/.

Run from the repository root: ``python3 scripts/make_invalid_fixtures.py``.
Each file is named after the issue code it must trigger (the tests rely on this).
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "invalid"


def pt(lon: object, lat: object, aid: object = "BENCH-0001", **props: object) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"asset_id": aid, **props},
    }


def fc(*features: object, **extra: object) -> dict[str, object]:
    return {"type": "FeatureCollection", **extra, "features": list(features)}


CASES: dict[str, object] = {
    "not_feature_collection": {"type": "Feature", "geometry": None, "properties": {}},
    "unsupported_geometry": fc(
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[-96.8, 32.78], [-96.79, 32.78]]},
            "properties": {"asset_id": "PATH-1"},
        }
    ),
    "null_geometry": fc({"type": "Feature", "geometry": None, "properties": {"asset_id": "X-1"}}),
    "coordinate_out_of_range": fc(pt(2484210.5, 6969120.25)),  # Texas State Plane feet
    "z_coordinate": fc(
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-96.8, 32.78, 140.0]},
            "properties": {"asset_id": "Z-1"},
        }
    ),
    "conflicting_crs": fc(
        pt(-96.8, 32.78),
        crs={"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2276"}},
    ),
    "duplicate_id": fc(pt(-96.8, 32.78, "BENCH-0001"), pt(-96.79, 32.78, "BENCH-0001")),
    "ambiguous_id": fc(pt(-96.8, 32.78, 142), pt(-96.79, 32.78, "142")),
    "null_id": fc(pt(-96.8, 32.78, None)),
    "empty_id": fc(pt(-96.8, 32.78, "   ")),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for code, doc in CASES.items():
        (OUT / f"{code}.geojson").write_text(json.dumps(doc, indent=1) + "\n")
    # NaN is not valid JSON, so it is written as raw text.
    (OUT / "nonfinite_value.geojson").write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature",'
        '"geometry":{"type":"Point","coordinates":[NaN,32.78]},'
        '"properties":{"asset_id":"N-1"}}]}\n'
    )
    (OUT / "invalid_json.geojson").write_text('{"type": "FeatureCollection", "features": [\n')
    print(f"wrote {len(CASES) + 2} files to {OUT}")


if __name__ == "__main__":
    main()
