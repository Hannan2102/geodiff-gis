"""Reproducible API walkthrough using the golden fixtures (Python standard library only).

    python3 scripts/api_walkthrough.py --api http://localhost:8000            # run steps
    python3 scripts/api_walkthrough.py --api http://localhost:8000 --verify    # after a restart

The run saves its workspace token and comparison ID to a local state file (in the system temp
directory, not the repository) so ``--verify`` can confirm decisions survived a restart.
``--cleanup`` deletes the workspace.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "fixtures" / "golden"
STATE = Path(tempfile.gettempdir()) / "geodiff-walkthrough.json"


def call(api: str, method: str, path: str, token: str | None = None, body: Any = None, raw: bytes | None = None) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if raw is not None:
        data = raw
        headers["Content-Type"] = "application/geo+json"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(api + path, data=data, method=method, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            text = r.read()
            return r.status, json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")


def run(api: str) -> None:
    step(1, "Create an anonymous workspace")
    s, ws = call(api, "POST", "/v1/workspaces")
    assert s == 201, ws
    tok = ws["token"]
    print(f"    workspace {ws['workspace_id']} expires {ws['expires_at']}")

    step(2, "Upload the approved baseline and the field inventory")
    ids = {}
    for name in ("baseline", "proposed"):
        s, d = call(api, "POST", f"/v1/datasets?name={name}.geojson", tok, raw=(GOLDEN / f"{name}.geojson").read_bytes())
        assert s == 201, d
        ids[name] = d["dataset_id"]
        print(f"    {name}: {d['feature_count']} features, sha256 {d['sha256'][:12]}…, candidate IDs {d['summary']['candidate_id_fields']}")

    step(3, "Compare with tolerance 2 m on condition/install_year/material/notes")
    s, c = call(api, "POST", "/v1/comparisons", tok, {
        "baseline_dataset_id": ids["baseline"], "proposed_dataset_id": ids["proposed"],
        "baseline_id_field": "asset_id", "compared_fields": ["condition", "install_year", "material", "notes"],
        "tolerance_m": 2.0,
    })
    assert s == 201, c
    cid = c["comparison_id"]
    print(f"    {c['summary']['affected_assets']} assets, {c['summary']['components']} components {c['summary']['components_by_kind']}")

    step(4, "Inspect BENCH-0142")
    s, a = call(api, "GET", f"/v1/comparisons/{cid}/assets/BENCH-0142", tok)
    for comp in a["components"]:
        ev = comp["evidence"]
        detail = f"{ev['distance_m']:.3f} m" if comp["kind"] == "move" else f"{ev['old']!r} -> {ev['new']!r}"
        print(f"    {comp['component_id']}: {detail} (version {comp['decision']['version']})")

    step(5, "Accept the condition update, reject the move")
    s, r = call(api, "POST", f"/v1/comparisons/{cid}/decisions", tok, {"decisions": [
        {"component_id": "BENCH-0142:attr:condition", "disposition": "accepted", "expected_version": 0},
        {"component_id": "BENCH-0142:move", "disposition": "rejected", "note": "Reported across 11th St; bench is anchored in the park.", "expected_version": 0},
    ]})
    assert s == 200, r
    print(f"    revision {r['revision']}; progress {r['progress']['by_disposition']}")

    step(6, "A stale tab tries to accept the move")
    s, r = call(api, "POST", f"/v1/comparisons/{cid}/decisions", tok, {"decisions": [
        {"component_id": "BENCH-0142:move", "disposition": "accepted", "expected_version": 0}]})
    print(f"    HTTP {s} {r['error']['code']}: {r['error']['message']}")
    assert s == 409

    step(7, "Final export is blocked while 7 components are pending; draft works")
    s, r = call(api, "GET", f"/v1/comparisons/{cid}/export?mode=final", tok)
    print(f"    final: HTTP {s} {r['error']['code']} ({r['error']['unresolved_count']} unresolved)")
    s, r = call(api, "GET", f"/v1/comparisons/{cid}/export?mode=draft&part=geojson", tok)
    bench = next(f for f in r["features"] if f["properties"]["asset_id"] == "BENCH-0142")
    print(f"    draft BENCH-0142: {bench['geometry']['coordinates']} condition={bench['properties']['condition']}")

    STATE.write_text(json.dumps({"api": api, "token": tok, "comparison_id": cid}))
    print(f"\nState saved to {STATE}. Restart the stack, then run with --verify.")


def verify(api: str) -> None:
    st = json.loads(STATE.read_text())
    tok, cid = st["token"], st["comparison_id"]
    step(8, "After restart: decisions persisted")
    s, a = call(api, "GET", f"/v1/comparisons/{cid}/assets/BENCH-0142", tok)
    assert s == 200, a
    got = {c["component_id"]: c["decision"]["disposition"] for c in a["components"]}
    print(f"    {got}")
    assert got == {"BENCH-0142:move": "rejected", "BENCH-0142:attr:condition": "accepted"}

    step(9, "Resolve the rest with the golden decisions and export final")
    golden = json.loads((GOLDEN / "decisions.json").read_text())
    s, q = call(api, "GET", f"/v1/comparisons/{cid}/queue?disposition=pending&limit=200", tok)
    items = [
        {"component_id": c["component_id"], "disposition": golden[c["component_id"]]["disposition"],
         "note": golden[c["component_id"]].get("note"), "expected_version": c["decision"]["version"]}
        for i in q["items"] for c in i["components"] if c["decision"]["disposition"] == "pending"
    ]
    s, r = call(api, "POST", f"/v1/comparisons/{cid}/decisions", tok, {"decisions": items})
    assert s == 200, r
    s, fc = call(api, "GET", f"/v1/comparisons/{cid}/export?mode=final&part=geojson", tok)
    expected = json.loads((GOLDEN / "expected_reviewed.geojson").read_text())
    print(f"    final export HTTP {s}; equals fixtures/golden/expected_reviewed.geojson: {fc == expected}")
    assert fc == expected


def cleanup(api: str) -> None:
    st = json.loads(STATE.read_text())
    s, _ = call(api, "DELETE", "/v1/workspace", st["token"])
    print(f"deleted workspace: HTTP {s}")
    STATE.unlink()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--cleanup", action="store_true")
    a = p.parse_args()
    if a.verify:
        verify(a.api.rstrip("/"))
    elif a.cleanup:
        cleanup(a.api.rstrip("/"))
    else:
        run(a.api.rstrip("/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
