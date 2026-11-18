"""Change detection between an approved baseline and a proposed (field) inventory.

Output is a versioned, JSON-serializable *comparison* (schema ``geodiff.comparison/1``):

* ``assets``: one entry per affected asset, grouped components, sorted by asset ID.
* Each component carries ``evidence`` (what changed) and a ``patch`` (how to apply it).
  Resolvers in Python (:mod:`.resolve`) and TypeScript apply patches mechanically; no rule
  is re-implemented outside this module.

Component kinds
  ``addition``  ID only in proposed; whole-feature decision; patch ``insert``.
  ``deletion``  ID only in baseline; whole-feature decision; patch ``remove``.
  ``move``      matched ID, geodesic distance strictly greater than the tolerance;
                patch ``set_coordinates``.
  ``attribute`` matched ID, one per selected field whose value differs; patch
                ``set_property`` or ``delete_property``.

Sub-threshold motion is not a component: the export keeps the baseline position. It is
counted in ``summary.within_tolerance_moves`` so near-threshold behavior stays visible.
"""

from __future__ import annotations

import copy
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from geodiff import __version__
from geodiff.engine.geodesy import DISTANCE_METHOD, geodesic_many
from geodiff.engine.geojson import ParsedDataset, is_scalar
from geodiff.engine.ids import id_kind, index_ids
from geodiff.engine.issues import Issue, IssueLog

SCHEMA = "geodiff.comparison/1"
LOW_OVERLAP_RATIO = 0.5

# Fields written by importers/editors rather than by field crews. They are never *suggested*
# for comparison (callers may still choose them explicitly).
TIMESTAMP_FIELD_RE = re.compile(
    r"(^|_)(imported|import|exported|export|created|creation|edited|edit|modified|updated|"
    r"last_edited|synced|loaded)(_?(at|date|time|timestamp|on|utc))?$|"
    r"^(editdate|creationdate|last_edited_date|created_date|globalid|objectid|fid|shape__.*)$",
    re.IGNORECASE,
)


class ComparisonError(ValueError):
    """The inputs cannot be compared; ``issues`` explains why."""

    def __init__(self, message: str, issues: IssueLog) -> None:
        super().__init__(message)
        self.issues = issues


@dataclass(frozen=True)
class CompareOptions:
    baseline_id_field: str
    proposed_id_field: str
    compare_fields: tuple[str, ...]
    tolerance_m: float = 2.0


def suggested_compare_fields(
    baseline: ParsedDataset, proposed: ParsedDataset, id_fields: set[str]
) -> list[str]:
    """Scalar fields present in both versions, excluding IDs and importer timestamps."""
    out = []
    for name, f in baseline.fields.items():
        g = proposed.fields.get(name)
        if g is None or name in id_fields or TIMESTAMP_FIELD_RE.search(name):
            continue
        if f.to_dict(baseline.input_count)["scalar"] and g.to_dict(proposed.input_count)["scalar"]:
            out.append(name)
    return sorted(out)


def values_equal(a: Any, b: Any) -> bool:
    """Scalar equality that keeps JSON types distinct: "5" != 5, true != 1, null != absent.

    Integers and floats are both JSON numbers and compare numerically (5 == 5.0).
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b
    if type(a) is not type(b):
        return False
    return bool(a == b)


_ABSENT = object()


def _added_feature(feat: dict[str, Any], opts: CompareOptions, raw_id: Any) -> dict[str, Any]:
    """Feature inserted on an accepted addition, keyed by the baseline ID field."""
    props = copy.deepcopy(feat.get("properties") or {})
    if opts.proposed_id_field != opts.baseline_id_field:
        props.pop(opts.proposed_id_field, None)
        props[opts.baseline_id_field] = raw_id
    out: dict[str, Any] = {
        "type": "Feature",
        "geometry": copy.deepcopy(feat["geometry"]),
        "properties": props,
    }
    if "id" in feat:
        out["id"] = feat["id"]
    return out


def check_options(
    baseline: ParsedDataset, proposed: ParsedDataset, opts: CompareOptions
) -> IssueLog:
    log = IssueLog()
    if not math.isfinite(opts.tolerance_m) or opts.tolerance_m < 0:
        log.add(
            Issue("invalid_tolerance", "Tolerance must be a finite, non-negative number of meters.")
        )
    for f in opts.compare_fields:
        if f in (opts.baseline_id_field, opts.proposed_id_field):
            log.add(
                Issue("id_field_compared", f"{f!r} is an ID field and cannot be compared.", field=f)
            )
            continue
        for ds in (baseline, proposed):
            fs = ds.fields.get(f)
            if fs is not None and not fs.to_dict(ds.input_count)["scalar"]:
                log.add(
                    Issue(
                        "nested_compare_field",
                        f"Field {f!r} in {ds.name} contains object or array values.",
                        field=f,
                    )
                )
    return log


def compare_datasets(
    baseline: ParsedDataset,
    proposed: ParsedDataset,
    opts: CompareOptions,
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Compare two validated datasets. Raises :class:`ComparisonError` with issues."""
    log = IssueLog()
    log.extend(baseline.issues)
    log.extend(proposed.issues)
    if log.has_errors:
        raise ComparisonError("One or both files failed validation.", log)
    base_idx, base_log = index_ids(baseline.features, opts.baseline_id_field)
    prop_idx, prop_log = index_ids(proposed.features, opts.proposed_id_field)
    log.extend(base_log)
    log.extend(prop_log)
    log.extend(check_options(baseline, proposed, opts))
    if log.has_errors:
        raise ComparisonError("The chosen ID fields or comparison options are invalid.", log)

    warnings = IssueLog()
    matched = sorted(base_idx.keys() & prop_idx.keys())
    added = sorted(prop_idx.keys() - base_idx.keys())
    deleted = sorted(base_idx.keys() - prop_idx.keys())

    smaller = min(len(base_idx), len(prop_idx))
    overlap = len(matched) / smaller if smaller else 1.0
    if smaller and overlap < LOW_OVERLAP_RATIO:
        warnings.add(
            Issue(
                "low_id_overlap",
                f"Only {len(matched)} of {smaller} IDs ({overlap:.0%}) match between versions.",
                severity="warning",
            )
        )
        stripped_base = {k.lstrip("0") for k in base_idx}
        stripped_hits = sum(1 for k in prop_idx if k.lstrip("0") in stripped_base)
        if stripped_hits > len(matched) * 2 and stripped_hits / smaller >= LOW_OVERLAP_RATIO:
            warnings.add(
                Issue(
                    "leading_zero_hint",
                    f"{stripped_hits} IDs would match if leading zeros were ignored.",
                    severity="warning",
                )
            )

    def feat_of(ds: ParsedDataset, idx: dict[str, int], aid: str) -> dict[str, Any]:
        return ds.features[idx[aid]]

    def coords(f: dict[str, Any]) -> list[float]:
        c = f["geometry"]["coordinates"]
        return [c[0], c[1]]

    type_mismatch = 0
    for aid in matched:
        b = feat_of(baseline, base_idx, aid)["properties"][opts.baseline_id_field]
        p = feat_of(proposed, prop_idx, aid)["properties"][opts.proposed_id_field]
        if id_kind(b) != id_kind(p):
            type_mismatch += 1
    if type_mismatch:
        warnings.add(
            Issue(
                "id_type_mismatch",
                f"{type_mismatch} matched IDs are numbers in one version and strings in the other.",
                severity="warning",
            )
        )

    bc = [coords(feat_of(baseline, base_idx, a)) for a in matched]
    pc = [coords(feat_of(proposed, prop_idx, a)) for a in matched]
    dist, az = geodesic_many(
        [c[0] for c in bc], [c[1] for c in bc], [c[0] for c in pc], [c[1] for c in pc]
    )

    assets: list[dict[str, Any]] = []
    within_tolerance = 0
    unchanged = 0
    for aid in deleted:
        f = feat_of(baseline, base_idx, aid)
        assets.append(
            {
                "asset_id": aid,
                "status": "deleted",
                "baseline": {"coordinates": coords(f), "properties": f["properties"]},
                "proposed": None,
                "components": [
                    {
                        "id": f"{aid}:deletion",
                        "kind": "deletion",
                        "evidence": {"coordinates": coords(f)},
                        "patch": {"op": "remove"},
                    }
                ],
            }
        )
    for aid in added:
        f = feat_of(proposed, prop_idx, aid)
        raw_id = f["properties"][opts.proposed_id_field]
        assets.append(
            {
                "asset_id": aid,
                "status": "added",
                "baseline": None,
                "proposed": {"coordinates": coords(f), "properties": f["properties"]},
                "components": [
                    {
                        "id": f"{aid}:addition",
                        "kind": "addition",
                        "evidence": {"coordinates": coords(f)},
                        "patch": {"op": "insert", "feature": _added_feature(f, opts, raw_id)},
                    }
                ],
            }
        )
    for k, aid in enumerate(matched):
        bf = feat_of(baseline, base_idx, aid)
        pf = feat_of(proposed, prop_idx, aid)
        comps: list[dict[str, Any]] = []
        # Identical coordinates are exactly zero; the vectorized solver can return ~1e-10 m.
        d = 0.0 if bc[k] == pc[k] else float(dist[k])
        if d > opts.tolerance_m:
            comps.append(
                {
                    "id": f"{aid}:move",
                    "kind": "move",
                    "evidence": {
                        "from": bc[k],
                        "to": pc[k],
                        "distance_m": d,
                        "bearing_deg": float(az[k]),
                        "tolerance_m": opts.tolerance_m,
                    },
                    "patch": {"op": "set_coordinates", "coordinates": pc[k]},
                }
            )
        elif d > 0:
            within_tolerance += 1
        bp, pp = bf["properties"], pf["properties"]
        for field in opts.compare_fields:
            old = bp.get(field, _ABSENT)
            new = pp.get(field, _ABSENT)
            if old is _ABSENT and new is _ABSENT:
                continue
            if old is not _ABSENT and new is not _ABSENT and values_equal(old, new):
                continue
            if not (is_scalar(old) or old is _ABSENT) or not (is_scalar(new) or new is _ABSENT):
                continue  # rejected earlier by check_options; defensive
            patch: dict[str, Any] = (
                {"op": "delete_property", "key": field}
                if new is _ABSENT
                else {"op": "set_property", "key": field, "value": new}
            )
            comps.append(
                {
                    "id": f"{aid}:attr:{field}",
                    "kind": "attribute",
                    "field": field,
                    "evidence": {
                        "field": field,
                        "old": None if old is _ABSENT else old,
                        "new": None if new is _ABSENT else new,
                        "old_present": old is not _ABSENT,
                        "new_present": new is not _ABSENT,
                    },
                    "patch": patch,
                }
            )
        if not comps:
            unchanged += 1
            continue
        assets.append(
            {
                "asset_id": aid,
                "status": "modified",
                "baseline": {"coordinates": bc[k], "properties": bp},
                "proposed": {"coordinates": pc[k], "properties": pp},
                "components": comps,
            }
        )

    assets.sort(key=lambda a: a["asset_id"])
    by_kind = Counter(c["kind"] for a in assets for c in a["components"])
    return {
        "schema": SCHEMA,
        "engine_version": __version__,
        "created_at": created_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "parameters": {
            "tolerance_m": opts.tolerance_m,
            "tolerance_rule": "move when geodesic distance > tolerance_m (strict)",
            "units": "meters",
            "distance_method": DISTANCE_METHOD,
            "baseline_id_field": opts.baseline_id_field,
            "proposed_id_field": opts.proposed_id_field,
            "compared_fields": list(opts.compare_fields),
            "id_policy": "NFC + trim strings; integers canonical decimal; case and leading zeros significant",
        },
        "inputs": {
            "baseline": {
                "name": baseline.name,
                "sha256": baseline.sha256,
                "feature_count": baseline.feature_count,
            },
            "proposed": {
                "name": proposed.name,
                "sha256": proposed.sha256,
                "feature_count": proposed.feature_count,
            },
        },
        "summary": {
            "baseline_assets": len(base_idx),
            "proposed_assets": len(prop_idx),
            "matched_assets": len(matched),
            "unchanged_assets": unchanged,
            "within_tolerance_moves": within_tolerance,
            "affected_assets": len(assets),
            "components": sum(by_kind.values()),
            "components_by_kind": {
                k: by_kind.get(k, 0) for k in ("addition", "deletion", "move", "attribute")
            },
            "id_overlap": round(overlap, 4),
        },
        "warnings": warnings.to_dict()["sample"],
        "assets": assets,
    }


def baseline_snapshot(baseline: ParsedDataset, id_field: str) -> list[dict[str, Any]]:
    """Normalized baseline features keyed by asset ID, in deterministic ID order."""
    idx, log = index_ids(baseline.features, id_field)
    if log.has_errors:
        raise ComparisonError("Baseline IDs are invalid.", log)
    return [{"asset_id": aid, "feature": baseline.features[idx[aid]]} for aid in sorted(idx)]
