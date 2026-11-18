"""Parse and validate RFC 7946 GeoJSON point inventories.

Validation is strict about geometry and lenient about properties: any JSON object is a valid
``properties`` member, but only scalar values can be *compared* (see :mod:`.compare`).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

from geodiff.engine.issues import Issue, IssueLog

WGS84_CRS_NAMES = {
    "urn:ogc:def:crs:OGC:1.3:CRS84",
    "urn:ogc:def:crs:OGC::CRS84",
    "OGC:CRS84",
    "CRS84",
    "urn:ogc:def:crs:EPSG::4326",
    "EPSG:4326",
}


class _NonFinite:
    """Placeholder for NaN/Infinity tokens so they can be reported, not crash parsing."""

    __slots__ = ("token",)

    def __init__(self, token: str) -> None:
        self.token = token

    def __repr__(self) -> str:
        return self.token


def _parse_float(s: str) -> float | _NonFinite:
    v = float(s)
    return v if math.isfinite(v) else _NonFinite(s)


def _contains_nonfinite(value: Any) -> bool:
    if isinstance(value, _NonFinite):
        return True
    if isinstance(value, dict):
        return any(_contains_nonfinite(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_nonfinite(v) for v in value)
    return False


def is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


@dataclass
class FieldSummary:
    name: str
    present: int = 0
    null: int = 0
    types: set[str] = field(default_factory=set)
    distinct: set[Any] = field(default_factory=set)
    distinct_capped: bool = False

    def to_dict(self, feature_count: int) -> dict[str, Any]:
        return {
            "name": self.name,
            "present": self.present,
            "null": self.null,
            "coverage": round(self.present / feature_count, 4) if feature_count else 0.0,
            "types": sorted(self.types),
            "scalar": self.types <= {"string", "number", "boolean", "null"},
        }


@dataclass
class ParsedDataset:
    name: str
    sha256: str
    size_bytes: int
    features: list[dict[str, Any]]
    issues: IssueLog
    fields: dict[str, FieldSummary]
    declared_crs: str | None = None
    input_count: int = 0

    @property
    def feature_count(self) -> int:
        return len(self.features)

    @property
    def valid(self) -> bool:
        return not self.issues.has_errors

    def candidate_id_fields(self) -> list[str]:
        """Fields present, non-null, string/number typed and distinct in every feature."""
        from geodiff.engine.ids import index_ids

        out: list[str] = []
        n = self.feature_count
        for f in self.fields.values():
            if n == 0 or f.present != n or f.null or not f.types <= {"string", "number"}:
                continue
            _, log = index_ids(self.features, f.name)
            if not log.has_errors:
                out.append(f.name)
        return out

    def summary(self) -> dict[str, Any]:
        n = self.input_count
        return {
            "name": self.name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "feature_count": self.input_count,
            "declared_crs": self.declared_crs,
            "fields": [f.to_dict(n) for f in sorted(self.fields.values(), key=lambda f: f.name)],
            "candidate_id_fields": self.candidate_id_fields() if self.valid else [],
            "validation": self.issues.to_dict(),
            "valid": self.valid,
        }


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_crs(doc: dict[str, Any], log: IssueLog) -> str | None:
    crs = doc.get("crs")
    if crs is None:
        return None
    name: Any = None
    if isinstance(crs, dict):
        props = crs.get("properties")
        if isinstance(props, dict):
            name = props.get("name") or props.get("href")
    name_s = str(name) if name is not None else repr(crs)
    if name_s in WGS84_CRS_NAMES:
        log.add(
            Issue(
                "legacy_crs_member",
                f"Legacy 'crs' member declares {name_s}; treated as WGS84 longitude/latitude.",
                severity="warning",
            )
        )
    else:
        log.add(
            Issue(
                "conflicting_crs",
                f"The file declares CRS {name_s}, which conflicts with RFC 7946 WGS84.",
                value=name_s,
            )
        )
    return name_s


def _check_point(geom: Any, i: int, log: IssueLog) -> bool:
    if geom is None:
        log.add(Issue("null_geometry", f"Feature {i} has null geometry.", feature_index=i))
        return False
    if not isinstance(geom, dict) or geom.get("type") != "Point":
        gtype = geom.get("type") if isinstance(geom, dict) else type(geom).__name__
        log.add(
            Issue(
                "unsupported_geometry",
                f"Feature {i} has geometry type {gtype}; only Point is supported.",
                feature_index=i,
                value=str(gtype),
            )
        )
        return False
    coords = geom.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        log.add(
            Issue(
                "invalid_coordinates",
                f"Feature {i} Point coordinates are not a [lon, lat] array.",
                feature_index=i,
            )
        )
        return False
    if any(isinstance(c, _NonFinite) for c in coords):
        log.add(
            Issue(
                "nonfinite_value",
                f"Feature {i} has a NaN/Infinity coordinate.",
                feature_index=i,
                field="geometry",
            )
        )
        return False
    if not all(isinstance(c, int | float) and not isinstance(c, bool) for c in coords):
        log.add(
            Issue(
                "invalid_coordinates",
                f"Feature {i} has a non-numeric coordinate.",
                feature_index=i,
            )
        )
        return False
    if len(coords) > 2:
        log.add(
            Issue(
                "z_coordinate",
                f"Feature {i} has {len(coords)} coordinate values; only 2D is supported.",
                feature_index=i,
            )
        )
        return False
    lon, lat = float(coords[0]), float(coords[1])
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        log.add(
            Issue(
                "coordinate_out_of_range",
                f"Feature {i} has coordinates ({lon}, {lat}) outside WGS84 bounds.",
                feature_index=i,
                value=[lon, lat],
            )
        )
        return False
    return True


def parse_geojson(
    data: bytes, name: str = "upload.geojson", max_features: int = 10_000
) -> ParsedDataset:
    """Parse ``data`` and collect every validation issue (sampled) instead of failing fast."""
    log = IssueLog()
    digest = sha256_hex(data)
    ds = ParsedDataset(name, digest, len(data), [], log, {})
    try:
        text = data.decode("utf-8-sig")
        doc = json.loads(text, parse_float=_parse_float, parse_constant=_NonFinite)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.add(Issue("invalid_json", f"The file is not valid UTF-8 JSON: {exc}"))
        return ds
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
        found = doc.get("type") if isinstance(doc, dict) else type(doc).__name__
        log.add(Issue("not_feature_collection", f"Top-level type is {found!r}."))
        return ds
    features = doc.get("features")
    if not isinstance(features, list):
        log.add(Issue("not_feature_collection", "'features' is missing or not an array."))
        return ds
    ds.declared_crs = _check_crs(doc, log)
    ds.input_count = len(features)
    if len(features) > max_features:
        log.add(
            Issue(
                "too_many_features",
                f"The file has {len(features)} features; the limit is {max_features}.",
            )
        )
        return ds

    for i, feat in enumerate(features):
        if not isinstance(feat, dict) or feat.get("type") != "Feature":
            log.add(Issue("not_feature", f"Element {i} is not a Feature.", feature_index=i))
            continue
        ok = _check_point(feat.get("geometry"), i, log)
        props = feat.get("properties")
        if props is None:
            props = {}
        if not isinstance(props, dict):
            log.add(
                Issue("not_feature", f"Feature {i} properties is not an object.", feature_index=i)
            )
            continue
        bad = [k for k, v in props.items() if _contains_nonfinite(v)]
        for k in bad:
            log.add(
                Issue(
                    "nonfinite_value",
                    f"Feature {i} property {k!r} contains NaN/Infinity.",
                    feature_index=i,
                    field=k,
                )
            )
        for k, v in props.items():
            fs = ds.fields.get(k)
            if fs is None:
                fs = ds.fields[k] = FieldSummary(k)
            fs.present += 1
            if v is None:
                fs.null += 1
            fs.types.add(value_type(v))
        if ok and not bad:
            out: dict[str, Any] = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        feat["geometry"]["coordinates"][0],
                        feat["geometry"]["coordinates"][1],
                    ],
                },
                "properties": props,
            }
            if "id" in feat:
                out["id"] = feat["id"]
            ds.features.append(out)
    if log.has_errors:
        ds.features = []
    return ds
