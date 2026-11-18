from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from geodiff.engine.compare import CompareOptions, compare_datasets
from geodiff.engine.geojson import ParsedDataset, parse_geojson

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
GOLDEN = FIXTURES / "golden"
GOLDEN_FIELDS = ("condition", "install_year", "material", "notes")


def load(path: Path) -> ParsedDataset:
    return parse_geojson(path.read_bytes(), name=path.name)


def from_obj(obj: Any, name: str = "mem.geojson") -> ParsedDataset:
    return parse_geojson(json.dumps(obj).encode(), name=name)


def golden_comparison(tolerance: float = 2.0) -> tuple[ParsedDataset, dict[str, Any]]:
    base = load(GOLDEN / "baseline.geojson")
    prop = load(GOLDEN / "proposed.geojson")
    opts = CompareOptions("asset_id", "asset_id", GOLDEN_FIELDS, tolerance)
    return base, compare_datasets(base, prop, opts, created_at="2026-11-18T00:00:00+00:00")


def pt(lon: float, lat: float, aid: Any, **props: Any) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"asset_id": aid, **props},
    }


def fc(*features: dict[str, Any]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}
