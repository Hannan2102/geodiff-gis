"""GeoDiff command line.

Examples::

    geodiff validate fixtures/golden/baseline.geojson
    geodiff compare fixtures/golden/baseline.geojson fixtures/golden/proposed.geojson \\
        --id-field asset_id --fields condition,material --tolerance 2 --out evidence.json
    geodiff resolve fixtures/golden/baseline.geojson evidence.json decisions.json \\
        --out reviewed.geojson --report report.json [--final]
    geodiff generate --out ../fixtures
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from geodiff import __version__
from geodiff.engine.compare import (
    CompareOptions,
    ComparisonError,
    baseline_snapshot,
    compare_datasets,
    suggested_compare_fields,
)
from geodiff.engine.geojson import ParsedDataset, parse_geojson
from geodiff.engine.resolve import FinalExportBlocked, resolve


def _load(path: str) -> ParsedDataset:
    p = Path(path)
    return parse_geojson(p.read_bytes(), name=p.name)


def _write(obj: Any, out: str | None) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def cmd_validate(args: argparse.Namespace) -> int:
    ds = _load(args.file)
    _write(ds.summary(), args.out)
    return 0 if ds.valid else 2


def _options(args: argparse.Namespace, base: ParsedDataset, prop: ParsedDataset) -> CompareOptions:
    pid = args.proposed_id_field or args.id_field
    if args.fields:
        fields = tuple(f for f in args.fields.split(",") if f)
    else:
        fields = tuple(suggested_compare_fields(base, prop, {args.id_field, pid}))
    return CompareOptions(args.id_field, pid, fields, args.tolerance)


def cmd_compare(args: argparse.Namespace) -> int:
    base, prop = _load(args.baseline), _load(args.proposed)
    try:
        result = compare_datasets(base, prop, _options(args, base, prop))
    except ComparisonError as exc:
        _write({"error": str(exc), "validation": exc.issues.to_dict()}, None)
        return 2
    _write(result, args.out)
    s = result["summary"]
    print(
        f"assets affected: {s['affected_assets']}  components: {s['components']}  "
        f"{s['components_by_kind']}  within-tolerance moves: {s['within_tolerance_moves']}",
        file=sys.stderr,
    )
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    base = _load(args.baseline)
    comparison = json.loads(Path(args.comparison).read_text())
    decisions = json.loads(Path(args.decisions).read_text()) if args.decisions else {}
    snapshot = baseline_snapshot(base, comparison["parameters"]["baseline_id_field"])
    try:
        fc, report = resolve(
            snapshot, comparison, decisions, mode="final" if args.final else "draft"
        )
    except FinalExportBlocked as exc:
        print(f"final export blocked: {exc}", file=sys.stderr)
        return 3
    _write(fc, args.out)
    if args.report:
        _write(report, args.report)
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    from geodiff import synth

    out = Path(args.out)
    base, prop, manifest = synth.generate(synth.Counts(n=args.n), seed=args.seed)
    d = out / "synthetic"
    synth.dump(base, d / f"baseline_{args.n}.geojson")
    synth.dump(prop, d / f"proposed_{args.n}.geojson")
    synth.dump_pretty(manifest, d / f"manifest_{args.n}.json")
    for name, fc in synth.context_layers().items():
        synth.dump(fc, out / "context" / f"{name}.geojson")
    print(f"wrote {d} (n={args.n}, seed={args.seed}) and {out / 'context'}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="geodiff", description="Point-inventory change review.")
    parser.add_argument("--version", action="version", version=f"geodiff {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="validate a GeoJSON point inventory")
    v.add_argument("file")
    v.add_argument("--out")
    v.set_defaults(func=cmd_validate)

    c = sub.add_parser("compare", help="compare baseline and proposed inventories")
    c.add_argument("baseline")
    c.add_argument("proposed")
    c.add_argument("--id-field", required=True)
    c.add_argument("--proposed-id-field")
    c.add_argument("--fields", help="comma-separated fields to compare (default: suggested)")
    c.add_argument("--tolerance", type=float, default=2.0, help="move tolerance in meters")
    c.add_argument("--out")
    c.set_defaults(func=cmd_compare)

    r = sub.add_parser("resolve", help="apply decisions and export a reviewed inventory")
    r.add_argument("baseline")
    r.add_argument("comparison")
    r.add_argument("decisions", nargs="?")
    r.add_argument("--out")
    r.add_argument("--report")
    r.add_argument("--final", action="store_true")
    r.set_defaults(func=cmd_resolve)

    g = sub.add_parser("generate", help="write deterministic synthetic fixtures")
    g.add_argument("--out", default="../fixtures")
    g.add_argument("--n", type=int, default=5000)
    g.add_argument("--seed", type=int, default=20261118)
    g.set_defaults(func=cmd_generate)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
