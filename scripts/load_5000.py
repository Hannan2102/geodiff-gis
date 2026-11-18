"""Exercise the API with the synthetic 5,000-asset pair and print timings.

Prerequisite: ``make fixtures``. Usage: ``python3 scripts/load_5000.py --api http://localhost:8000``.
Deletes its workspace at the end.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api_walkthrough import call  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SYN = ROOT / "fixtures" / "synthetic"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://localhost:8000")
    api = p.parse_args().api.rstrip("/")
    s, ws = call(api, "POST", "/v1/workspaces")
    tok = ws["token"]
    ids = {}
    for name in ("baseline", "proposed"):
        data = (SYN / f"{name}_5000.geojson").read_bytes()
        t = time.perf_counter()
        s, d = call(api, "POST", f"/v1/datasets?name={name}_5000.geojson", tok, raw=data)
        print(f"upload {name}: HTTP {s} {len(data) / 1e6:.2f} MB in {time.perf_counter() - t:.2f}s")
        ids[name] = d["dataset_id"]
    t = time.perf_counter()
    s, c = call(api, "POST", "/v1/comparisons", tok, {
        "baseline_dataset_id": ids["baseline"], "proposed_dataset_id": ids["proposed"],
        "baseline_id_field": "asset_id",
        "compared_fields": ["asset_type", "condition", "install_year", "last_inspected", "material", "notes", "park"],
        "tolerance_m": 2.0,
    })
    print(f"compare: HTTP {s} in {time.perf_counter() - t:.2f}s -> {c['summary']['components']} components")
    cid = c["comparison_id"]
    t = time.perf_counter()
    s, q = call(api, "GET", f"/v1/comparisons/{cid}/queue?limit=200", tok)
    print(f"queue page (200 assets): HTTP {s} in {time.perf_counter() - t:.3f}s")
    t = time.perf_counter()
    s, m = call(api, "GET", f"/v1/comparisons/{cid}/map?bbox=-96.82,32.77,-96.77,32.80&limit=2000", tok)
    print(f"map viewport: HTTP {s} {len(m['features'])} features in {time.perf_counter() - t:.3f}s")
    t = time.perf_counter()
    s, e = call(api, "GET", f"/v1/comparisons/{cid}/export?mode=draft", tok)
    print(f"draft export: HTTP {s} {len(e['geojson']['features'])} features in {time.perf_counter() - t:.2f}s")
    call(api, "DELETE", "/v1/workspace", tok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
