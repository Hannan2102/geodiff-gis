"""Resolve review decisions into a reconciled inventory and a machine-readable report.

Rules (section 3.6 of the brief):

* ``accepted`` applies the component's patch. ``rejected``, ``pending`` and ``flagged`` keep
  the baseline.
* An accepted addition inserts the proposed feature; an accepted deletion removes the
  baseline feature. Rejecting either preserves baseline membership. A pending addition is
  therefore *omitted* from a draft export, and the report says so.
* A flagged component needs a note and stays unresolved.
* A final export requires every component to be accepted or rejected; a draft export is
  always available and lists unresolved components separately.
* Output features are ordered by asset ID (Unicode code point order).

The TypeScript resolver in ``web/src/sample/resolve.ts`` implements these same patch
semantics for the static demo; ``tests/test_parity.py`` checks both on identical input.
"""

from __future__ import annotations

import copy
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Literal

REPORT_SCHEMA = "geodiff.report/1"
DISPOSITIONS = ("pending", "accepted", "rejected", "flagged")
Mode = Literal["draft", "final"]


class FinalExportBlocked(ValueError):
    def __init__(self, unresolved: list[dict[str, Any]]) -> None:
        super().__init__(
            f"{len(unresolved)} components are unresolved; final export needs all resolved."
        )
        self.unresolved = unresolved


class InvalidDecision(ValueError):
    pass


def check_decision(decision: dict[str, Any]) -> None:
    disp = decision.get("disposition", "pending")
    if disp not in DISPOSITIONS:
        raise InvalidDecision(f"Unknown disposition {disp!r}.")
    if disp == "flagged" and not str(decision.get("note") or "").strip():
        raise InvalidDecision("Flagging requires a note.")


def apply_patch(features: dict[str, dict[str, Any]], asset_id: str, patch: dict[str, Any]) -> None:
    op = patch["op"]
    if op == "insert":
        features[asset_id] = copy.deepcopy(patch["feature"])
    elif op == "remove":
        features.pop(asset_id, None)
    elif op == "set_coordinates":
        features[asset_id]["geometry"]["coordinates"] = list(patch["coordinates"])
    elif op == "set_property":
        features[asset_id]["properties"][patch["key"]] = copy.deepcopy(patch["value"])
    elif op == "delete_property":
        features[asset_id]["properties"].pop(patch["key"], None)
    else:
        raise ValueError(f"Unknown patch op {op!r}")


def resolve(
    snapshot: list[dict[str, Any]],
    comparison: dict[str, Any],
    decisions: dict[str, dict[str, Any]],
    *,
    mode: Mode = "draft",
    exported_at: str | None = None,
    attribution: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(feature_collection, report)``. Raises :class:`FinalExportBlocked`."""
    features = {row["asset_id"]: copy.deepcopy(row["feature"]) for row in snapshot}
    known = {c["id"] for a in comparison["assets"] for c in a["components"]}
    unknown = sorted(set(decisions) - known)
    if unknown:
        raise InvalidDecision(f"Decisions reference unknown components: {unknown[:5]}")

    rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    by_disp: Counter[str] = Counter()
    by_kind_disp: dict[str, Counter[str]] = {}
    omitted_pending_additions: list[str] = []
    for asset in comparison["assets"]:
        aid = asset["asset_id"]
        for comp in asset["components"]:
            dec = decisions.get(comp["id"], {})
            check_decision(dec)
            disp = dec.get("disposition", "pending")
            by_disp[disp] += 1
            by_kind_disp.setdefault(comp["kind"], Counter())[disp] += 1
            row = {
                "component_id": comp["id"],
                "asset_id": aid,
                "kind": comp["kind"],
                "field": comp.get("field"),
                "disposition": disp,
                "note": dec.get("note"),
                "decided_at": dec.get("decided_at"),
            }
            rows.append(row)
            if disp == "accepted":
                apply_patch(features, aid, comp["patch"])
            elif disp in ("pending", "flagged"):
                unresolved.append(row)
                if comp["kind"] == "addition":
                    omitted_pending_additions.append(aid)

    if mode == "final" and unresolved:
        raise FinalExportBlocked(unresolved)

    ordered = [features[k] for k in sorted(features)]
    fc: dict[str, Any] = {"type": "FeatureCollection", "features": ordered}
    params = comparison["parameters"]
    report = {
        "schema": REPORT_SCHEMA,
        "mode": mode,
        "complete": not unresolved,
        "exported_at": exported_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "engine_version": comparison.get("engine_version"),
        "comparison_created_at": comparison.get("created_at"),
        "inputs": comparison["inputs"],
        "attribution": attribution
        or {"note": "Synthetic or user-supplied data; no external source attribution recorded."},
        "parameters": {
            "tolerance_m": params["tolerance_m"],
            "tolerance_rule": params.get("tolerance_rule"),
            "units": params.get("units", "meters"),
            "distance_method": params.get("distance_method"),
            "baseline_id_field": params["baseline_id_field"],
            "proposed_id_field": params["proposed_id_field"],
            "compared_fields": params["compared_fields"],
        },
        "counts": {
            "affected_assets": len(comparison["assets"]),
            "components": len(rows),
            "by_disposition": {d: by_disp.get(d, 0) for d in DISPOSITIONS},
            "by_kind": {
                k: {d: c.get(d, 0) for d in DISPOSITIONS} for k, c in sorted(by_kind_disp.items())
            },
            "unresolved_components": len(unresolved),
            "output_features": len(ordered),
            "baseline_features": len(snapshot),
        },
        "unresolved": unresolved,
        "notes": [
            f"Pending or flagged addition {aid} is omitted: additions are inserted only when accepted."
            for aid in omitted_pending_additions
        ]
        + (
            ["Rejected, pending and flagged components keep the baseline value and position."]
            if unresolved or by_disp.get("rejected")
            else []
        ),
        "decisions": rows,
        "history": history or [],
    }
    return fc, report
