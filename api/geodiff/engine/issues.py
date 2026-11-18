"""Validation issues: machine-readable code, human message and remediation guidance."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning"]

GUIDANCE: dict[str, str] = {
    "invalid_json": "Export the layer again as GeoJSON (UTF-8). In QGIS: right-click the layer, "
    "choose Export > Save Features As..., then pick format GeoJSON.",
    "not_feature_collection": "The file must be a GeoJSON FeatureCollection whose 'features' "
    "member is an array of Feature objects.",
    "not_feature": "Every element of 'features' must be an object with type 'Feature'.",
    "null_geometry": "Give every asset a location, or remove rows without geometry before "
    "export. In QGIS, select features with the expression 'geometry IS NULL'.",
    "unsupported_geometry": "GeoDiff v1 compares Point features only. Convert other geometry, or "
    "export only the point layer. MultiPoint must be split (Vector > Geometry Tools > "
    "Multipart to Singleparts).",
    "invalid_coordinates": "A Point needs exactly [longitude, latitude] as two numbers.",
    "z_coordinate": "GeoDiff v1 compares 2D points. Drop Z values in QGIS with "
    "Processing > Drop M/Z values, then export again.",
    "nonfinite_value": "NaN and Infinity are not valid JSON numbers. Replace them with null or "
    "a real value before export.",
    "coordinate_out_of_range": "Longitude must be within [-180, 180] and latitude within "
    "[-90, 90]. Projected coordinates (meters or feet) suggest the file is not in WGS84. "
    "Reproject in QGIS: Export > Save Features As..., CRS = EPSG:4326 - WGS 84.",
    "conflicting_crs": "RFC 7946 GeoJSON is always WGS84 longitude/latitude. Reproject in QGIS "
    "(Export > Save Features As..., CRS = EPSG:4326 - WGS 84) and do not write a legacy 'crs' "
    "member for another system.",
    "legacy_crs_member": "The legacy 'crs' member was accepted because it names WGS84. RFC 7946 "
    "files should omit it.",
    "too_many_features": "Split the inventory or raise the server limit in a private deployment.",
    "missing_id": "Every feature needs a value in the chosen ID field.",
    "null_id": "Every feature needs a non-null value in the chosen ID field.",
    "empty_id": "IDs cannot be empty or whitespace only.",
    "unsupported_id_type": "IDs must be strings or integers. Booleans, objects, arrays and "
    "fractional numbers are not accepted.",
    "duplicate_id": "Each asset ID must appear once per version. Resolve duplicates in the "
    "source before comparing.",
    "ambiguous_id": "Two different raw values normalize to the same ID (for example 142 and "
    "'142', or 'A1' and ' A1'). Make the IDs consistent in the source.",
    "nested_compare_field": "v1 compares scalar values (string, number, boolean, null). Choose "
    "another field, or flatten the property before export. Nested values are still "
    "preserved from the baseline in the export.",
    "id_field_compared": "The ID field identifies the asset and is never compared as an attribute.",
    "low_id_overlap": "Few IDs match between versions. Check that the right ID field is chosen "
    "for each file and that the IDs use the same format (see ID policy).",
    "id_type_mismatch": "The same ID is a number in one version and a string in the other. "
    "They are matched, but check the export schema.",
    "leading_zero_hint": "Many IDs would match if leading zeros were ignored (for example '0142' "
    "vs 142). GeoDiff treats leading zeros as significant; make the formats consistent.",
}


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: Severity = "error"
    feature_index: int | None = None
    field: str | None = None
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "guidance": GUIDANCE.get(self.code, ""),
        }
        if self.feature_index is not None:
            d["feature_index"] = self.feature_index
        if self.field is not None:
            d["field"] = self.field
        if self.value is not None:
            d["value"] = self.value
        return d


@dataclass
class IssueLog:
    """Keeps exact counts per code but only a bounded sample of individual issues."""

    sample_per_code: int = 20
    counts: Counter[str] = field(default_factory=Counter)
    severities: dict[str, Severity] = field(default_factory=dict)
    samples: list[Issue] = field(default_factory=list)

    def add(self, issue: Issue) -> None:
        self.counts[issue.code] += 1
        self.severities[issue.code] = issue.severity
        if self.counts[issue.code] <= self.sample_per_code:
            self.samples.append(issue)

    def extend(self, other: IssueLog) -> None:
        for code, n in other.counts.items():
            self.counts[code] += n
            self.severities[code] = other.severities[code]
        kept = Counter(i.code for i in self.samples)
        for issue in other.samples:
            if kept[issue.code] < self.sample_per_code:
                self.samples.append(issue)
                kept[issue.code] += 1

    @property
    def error_count(self) -> int:
        return sum(n for c, n in self.counts.items() if self.severities[c] == "error")

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_count": self.error_count,
            "warning_count": sum(
                n for c, n in self.counts.items() if self.severities[c] == "warning"
            ),
            "counts": dict(sorted(self.counts.items())),
            "sample": [i.to_dict() for i in self.samples],
            "sampled": any(n > self.sample_per_code for n in self.counts.values()),
        }
