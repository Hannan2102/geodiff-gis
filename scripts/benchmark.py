"""Reproducible GeoDiff benchmarks and detection accuracy.

Usage (from the repo root, stack running for the API part):

    cd api && uv run python ../scripts/benchmark.py all --api http://localhost:8000 \
        --out ../docs/benchmarks

Parts
- engine: in-process computation only (no HTTP, no database). Generates seeded synthetic pairs,
  times parse, compare and resolve, and scores detection against the generator's labels.
- api: client-observed wall time over loopback HTTP against a running stack (includes HTTP,
  JSON and PostGIS work). /health round trips are measured separately as the transport floor.
- cold: `docker compose restart api` until /ready answers (container restart, not a free-host
  wake-up; that is measured after deployment).

Timing: time.perf_counter; each measurement repeated --runs times (default 5, engine 7);
reports median, min and max. Nothing is cached between API runs (fresh workspace per run).
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api_walkthrough import call

from geodiff import __version__
from geodiff.engine.compare import CompareOptions, baseline_snapshot, compare_datasets
from geodiff.engine.geojson import parse_geojson
from geodiff.engine.resolve import resolve
from geodiff.synth import DEFAULT_COMPARE_FIELDS, SEED, TOLERANCE_M, Counts, generate

ROOT = Path(__file__).resolve().parent.parent
KINDS = ("addition", "deletion", "move", "attribute")


def counts_for(n: int) -> Counts:
    """Scale the default 5,000-asset change mix to n (near-threshold cases fixed at 10 each)."""
    d = Counts()
    f = n / d.n
    return Counts(
        n=n,
        deletions=round(d.deletions * f),
        additions=round(d.additions * f),
        moves=round(d.moves * f),
        move_and_edit=round(d.move_and_edit * f),
        edits=round(d.edits * f),
        jitter=round(d.jitter * f),
        near_below=10,
        near_above=10,
        id_changes=max(1, round(d.id_changes * f)),
        null_to_absent=max(1, round(d.null_to_absent * f)),
        absent_to_null=max(1, round(d.absent_to_null * f)),
        number_to_string=max(1, round(d.number_to_string * f)),
    )


def summarize(xs: list[float]) -> dict[str, float]:
    return {
        "median_s": round(statistics.median(xs), 4),
        "min_s": round(min(xs), 4),
        "max_s": round(max(xs), 4),
        "runs": len(xs),
    }


def timed(fn: Any, runs: int) -> tuple[dict[str, float], Any]:
    out = None
    xs = []
    for _ in range(runs):
        t = time.perf_counter()
        out = fn()
        xs.append(time.perf_counter() - t)
    return summarize(xs), out


def environment() -> dict[str, Any]:
    def sh(*cmd: str) -> str:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, check=True)  # noqa: S603
            return r.stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"

    import numpy
    import pyproj
    import shapely

    return {
        "cpu": sh("sysctl", "-n", "machdep.cpu.brand_string"),
        "cores": sh("sysctl", "-n", "hw.ncpu"),
        "memory_gb": round(int(sh("sysctl", "-n", "hw.memsize") or 0) / 2**30, 1)
        if sh("sysctl", "-n", "hw.memsize").isdigit()
        else "unknown",
        "os": f"macOS {sh('sw_vers', '-productVersion')} ({platform.machine()})",
        "python": platform.python_version(),
        "geodiff": __version__,
        "pyproj": pyproj.__version__,
        "proj": pyproj.proj_version_str,
        "shapely": shapely.__version__,
        "numpy": numpy.__version__,
        "docker": sh(
            "docker", "info", "--format", "{{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes"
        ),
        "git_sha": sh("git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"),
        "date": time.strftime("%Y-%m-%d"),
    }


# --------------------------------------------------------------------------- engine


def score(result: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    got = {c["id"]: c["kind"] for a in result["assets"] for c in a["components"]}
    want = {c["id"]: c["kind"] for a in manifest["assets"] for c in a["components"]}
    per: dict[str, Any] = {}
    for k in (*KINDS, "all"):
        g = {i for i, kk in got.items() if k in ("all", kk)}
        w = {i for i, kk in want.items() if k in ("all", kk)}
        tp, fp, fn = len(g & w), len(g - w), len(w - g)
        per[k] = {
            "labeled": len(w),
            "detected": len(g),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        }
    near = [c for a in manifest["assets"] for c in a["components"] if c.get("near_threshold")]
    controls = set(manifest["unchanged_controls"])
    affected = {a["asset_id"] for a in result["assets"]}
    per["negatives"] = {
        "within_tolerance_motions": len(controls),
        "falsely_flagged": len(controls & affected),
        "near_threshold_moves_labeled": len(near),
        "near_threshold_moves_detected": sum(1 for c in near if c["id"] in got),
    }
    return per


def engine_part(sizes: list[int], runs: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for n in sizes:
        base_fc, prop_fc, manifest = generate(counts_for(n), seed=SEED)
        b_bytes = json.dumps(base_fc).encode()
        p_bytes = json.dumps(prop_fc).encode()
        opts = CompareOptions("asset_id", "asset_id", DEFAULT_COMPARE_FIELDS, TOLERANCE_M)
        t_parse, (base, prop) = timed(
            lambda b=b_bytes, p=p_bytes: (
                parse_geojson(b, name="b.geojson", max_features=20_000),
                parse_geojson(p, name="p.geojson", max_features=20_000),
            ),
            runs,
        )
        t_cmp, result = timed(lambda b=base, p=prop, o=opts: compare_datasets(b, p, o), runs)
        snap = baseline_snapshot(base, "asset_id")
        t_res, _ = timed(lambda s=snap, r=result: resolve(s, r, {}, mode="draft"), runs)
        out[str(n)] = {
            "input_mb": round((len(b_bytes) + len(p_bytes)) / 1e6, 2),
            "baseline_features": len(base_fc["features"]),
            "proposed_features": len(prop_fc["features"]),
            "parse_both": t_parse,
            "compare": t_cmp,
            "resolve_draft_export": t_res,
            "summary": result["summary"],
            "accuracy": score(result, manifest),
        }
        a = out[str(n)]["accuracy"]["all"]
        print(
            f"engine n={n}: parse {t_parse['median_s']}s compare {t_cmp['median_s']}s "
            f"resolve {t_res['median_s']}s  P={a['precision']} R={a['recall']} "
            f"({a['tp']}/{a['labeled']})",
            file=sys.stderr,
        )
    return out


# --------------------------------------------------------------------------- api


def api_part(api: str, sizes: list[int], runs: int) -> dict[str, Any]:
    health = []
    for _ in range(50):
        t = time.perf_counter()
        call(api, "GET", "/health")
        health.append(time.perf_counter() - t)
    out: dict[str, Any] = {"health_round_trip": summarize(health)}
    for n in sizes:
        base_fc, prop_fc, _ = generate(counts_for(n), seed=SEED)
        data = {"baseline": json.dumps(base_fc).encode(), "proposed": json.dumps(prop_fc).encode()}
        rows: dict[str, list[float]] = {}

        def rec(name: str, t0: float, rows: dict[str, list[float]] = rows) -> None:
            rows.setdefault(name, []).append(time.perf_counter() - t0)

        components = 0
        for _ in range(runs):
            s, ws = call(api, "POST", "/v1/workspaces")
            assert s == 201, ws
            tok = ws["token"]
            ids = {}
            for role, raw in data.items():
                t = time.perf_counter()
                s, d = call(api, "POST", f"/v1/datasets?name={role}_{n}.geojson", tok, raw=raw)
                rec(f"upload_{role}", t)
                assert s == 201, d
                ids[role] = d["dataset_id"]
            t = time.perf_counter()
            s, c = call(
                api,
                "POST",
                "/v1/comparisons",
                tok,
                {
                    "baseline_dataset_id": ids["baseline"],
                    "proposed_dataset_id": ids["proposed"],
                    "baseline_id_field": "asset_id",
                    "compared_fields": list(DEFAULT_COMPARE_FIELDS),
                    "tolerance_m": TOLERANCE_M,
                },
            )
            rec("compare", t)
            assert s == 201, c
            components = c["summary"]["components"]
            cid = c["comparison_id"]
            t = time.perf_counter()
            s, q = call(api, "GET", f"/v1/comparisons/{cid}/queue?limit=200", tok)
            rec("queue_first_page", t)
            assert s == 200 and q["items"], q
            t = time.perf_counter()
            cursor, pages = None, 0
            while True:
                path = f"/v1/comparisons/{cid}/queue?limit=200" + (
                    f"&cursor={cursor}" if cursor else ""
                )
                s, q = call(api, "GET", path, tok)
                assert s == 200, q
                pages += 1
                cursor = q["next_cursor"]
                if not cursor:
                    break
            rec("queue_all_pages", t)
            ex = c["extent"]
            t = time.perf_counter()
            bbox = ",".join(map(str, ex))
            s, m = call(api, "GET", f"/v1/comparisons/{cid}/map?bbox={bbox}&limit=2000", tok)
            rec("map_full_extent", t)
            assert s == 200, m
            t = time.perf_counter()
            s, a = call(api, "GET", f"/v1/comparisons/{cid}/assets/BENCH-0142", tok)
            rec("asset_detail", t)
            assert s == 200, a
            comp = a["components"][0]
            t = time.perf_counter()
            s, dr = call(
                api,
                "POST",
                f"/v1/comparisons/{cid}/decisions",
                tok,
                {
                    "decisions": [
                        {
                            "component_id": comp["component_id"],
                            "disposition": "rejected",
                            "expected_version": 0,
                        }
                    ]
                },
            )
            rec("single_decision", t)
            assert s in (200, 201), dr
            t = time.perf_counter()
            s, e = call(api, "GET", f"/v1/comparisons/{cid}/export?mode=draft&part=bundle", tok)
            rec("draft_export", t)
            assert s == 200, str(e)[:300]
            call(api, "DELETE", "/v1/workspace", tok)
        out[str(n)] = {
            "components": components,
            "queue_pages": pages,
            **{k: summarize(v) for k, v in rows.items()},
        }
        print(
            f"api n={n}: compare {out[str(n)]['compare']['median_s']}s "
            f"queue page {out[str(n)]['queue_first_page']['median_s']}s",
            file=sys.stderr,
        )
    return out


def cold_part(api: str, runs: int) -> dict[str, Any]:
    xs = []
    for _ in range(runs):
        t = time.perf_counter()
        subprocess.run(
            ["docker", "compose", "restart", "api"],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        while True:
            try:
                s, r = call(api, "GET", "/ready")
                if s == 200 and r.get("status") == "ready":
                    break
            except OSError:
                pass
            time.sleep(0.1)
        xs.append(time.perf_counter() - t)
    print(f"cold restart to ready: {statistics.median(xs):.2f}s", file=sys.stderr)
    return {"restart_to_ready": summarize(xs)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("part", choices=["engine", "api", "cold", "all"])
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--out", default=str(ROOT / "docs" / "benchmarks"))
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    env = environment()
    if a.part in ("engine", "all"):
        res = engine_part([1000, 5000, 10000], max(a.runs, 7))
        (out / "engine.json").write_text(
            json.dumps({"environment": env, "seed": SEED, "results": res}, indent=2) + "\n"
        )
    if a.part in ("api", "all"):
        res = api_part(a.api.rstrip("/"), [1000, 5000], a.runs)
        (out / "api.json").write_text(
            json.dumps({"environment": env, "seed": SEED, "results": res}, indent=2) + "\n"
        )
    if a.part in ("cold", "all"):
        res = cold_part(a.api.rstrip("/"), 3)
        (out / "cold.json").write_text(
            json.dumps({"environment": env, "results": res}, indent=2) + "\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
