// Browser port of api/geodiff/engine/resolve.py for the static guided sample.
// It only applies the engine's precomputed patches; all detection happens in Python.
// Parity with the Python reference is tested in resolve.test.ts against fixtures/demo/parity.
import type { AssetSide, Disposition, Evidence, FeatureCollection, GeoFeature, Kind, Patch } from '../types';

export const REPORT_SCHEMA = 'geodiff.report/1';
export const DISPOSITIONS: readonly Disposition[] = ['pending', 'accepted', 'rejected', 'flagged'];

export interface EngineComponent {
  id: string;
  kind: Kind;
  field?: string;
  evidence: Evidence;
  patch: Patch;
}

export interface EngineAsset {
  asset_id: string;
  status: 'added' | 'deleted' | 'modified';
  baseline: AssetSide | null;
  proposed: AssetSide | null;
  components: EngineComponent[];
}

export interface EngineComparison {
  schema: string;
  engine_version: string;
  created_at: string;
  parameters: {
    tolerance_m: number;
    tolerance_rule?: string;
    units?: string;
    distance_method?: string;
    baseline_id_field: string;
    proposed_id_field: string;
    compared_fields: string[];
  };
  inputs: { baseline: { name: string; sha256: string; feature_count: number }; proposed: { name: string; sha256: string; feature_count: number } };
  summary: Record<string, unknown> & { affected_assets: number; components: number };
  warnings: { code: string; message: string; guidance: string }[];
  assets: EngineAsset[];
}

export interface SnapshotRow {
  asset_id: string;
  feature: GeoFeature;
}

export interface DecisionRecord {
  disposition: Disposition;
  note?: string | null;
  decided_at?: string | null;
}

export class FinalExportBlocked extends Error {
  constructor(public unresolved: Record<string, unknown>[]) {
    super(`${unresolved.length} component(s) are still pending or flagged.`);
  }
}

export class InvalidDecision extends Error {}

/** Python's sorted() on str: Unicode code point order (JS < compares UTF-16 code units). */
export function compareCodePoints(a: string, b: string): number {
  const ia = a[Symbol.iterator]();
  const ib = b[Symbol.iterator]();
  for (;;) {
    const x = ia.next();
    const y = ib.next();
    if (x.done || y.done) return x.done && y.done ? 0 : x.done ? -1 : 1;
    const cx = x.value.codePointAt(0)!;
    const cy = y.value.codePointAt(0)!;
    if (cx !== cy) return cx - cy;
  }
}

const clone = <T>(v: T): T => structuredClone(v);

export function checkDecision(d: DecisionRecord | undefined): void {
  const disp = d?.disposition ?? 'pending';
  if (!DISPOSITIONS.includes(disp)) throw new InvalidDecision(`Unknown disposition '${String(disp)}'.`);
  if (disp === 'flagged' && !String(d?.note ?? '').trim()) throw new InvalidDecision('Flagging requires a note.');
}

export function applyPatch(features: Map<string, GeoFeature>, assetId: string, patch: Patch): void {
  switch (patch.op) {
    case 'insert':
      features.set(assetId, clone(patch.feature!));
      return;
    case 'remove':
      features.delete(assetId);
      return;
    case 'set_coordinates':
      features.get(assetId)!.geometry.coordinates = [patch.coordinates![0], patch.coordinates![1]];
      return;
    case 'set_property':
      features.get(assetId)!.properties[patch.key!] = clone(patch.value);
      return;
    case 'delete_property':
      delete features.get(assetId)!.properties[patch.key!];
      return;
    default:
      throw new Error(`Unknown patch op ${String((patch as Patch).op)}`);
  }
}

export function resolveExport(
  snapshot: SnapshotRow[],
  comparison: EngineComparison,
  decisions: Record<string, DecisionRecord>,
  opts: { mode: 'draft' | 'final'; exportedAt?: string; attribution?: Record<string, unknown>; history?: unknown[] },
): { geojson: FeatureCollection; report: Record<string, unknown> & { complete: boolean; counts: { unresolved_components: number; output_features: number }; notes: string[] } } {
  const features = new Map<string, GeoFeature>(snapshot.map((r) => [r.asset_id, clone(r.feature)]));
  const known = new Set(comparison.assets.flatMap((a) => a.components.map((c) => c.id)));
  const unknown = Object.keys(decisions).filter((k) => !known.has(k)).sort(compareCodePoints);
  if (unknown.length) throw new InvalidDecision(`Decisions reference unknown components: ${unknown.slice(0, 5).join(', ')}`);

  const rows: Record<string, unknown>[] = [];
  const unresolved: Record<string, unknown>[] = [];
  const byDisp: Record<string, number> = {};
  const byKindDisp: Record<string, Record<string, number>> = {};
  const omittedPendingAdditions: string[] = [];
  for (const asset of comparison.assets) {
    const aid = asset.asset_id;
    for (const comp of asset.components) {
      const dec = decisions[comp.id];
      checkDecision(dec);
      const disp = dec?.disposition ?? 'pending';
      byDisp[disp] = (byDisp[disp] ?? 0) + 1;
      const k = (byKindDisp[comp.kind] ??= {});
      k[disp] = (k[disp] ?? 0) + 1;
      const row = {
        component_id: comp.id,
        asset_id: aid,
        kind: comp.kind,
        field: comp.field ?? null,
        disposition: disp,
        note: dec?.note ?? null,
        decided_at: dec?.decided_at ?? null,
      };
      rows.push(row);
      if (disp === 'accepted') applyPatch(features, aid, comp.patch);
      else if (disp === 'pending' || disp === 'flagged') {
        unresolved.push(row);
        if (comp.kind === 'addition') omittedPendingAdditions.push(aid);
      }
    }
  }
  if (opts.mode === 'final' && unresolved.length) throw new FinalExportBlocked(unresolved);

  const ordered = [...features.keys()].sort(compareCodePoints).map((k) => features.get(k)!);
  const p = comparison.parameters;
  const fill = (c: Record<string, number> | undefined) => Object.fromEntries(DISPOSITIONS.map((d) => [d, c?.[d] ?? 0]));
  const report = {
    schema: REPORT_SCHEMA,
    mode: opts.mode,
    complete: unresolved.length === 0,
    exported_at: opts.exportedAt ?? new Date().toISOString().replace(/\.\d{3}Z$/, '+00:00'),
    engine_version: comparison.engine_version,
    comparison_created_at: comparison.created_at,
    inputs: comparison.inputs,
    attribution: opts.attribution ?? { note: 'Synthetic or user-supplied data; no external source attribution recorded.' },
    parameters: {
      tolerance_m: p.tolerance_m,
      tolerance_rule: p.tolerance_rule ?? null,
      units: p.units ?? 'meters',
      distance_method: p.distance_method ?? null,
      baseline_id_field: p.baseline_id_field,
      proposed_id_field: p.proposed_id_field,
      compared_fields: p.compared_fields,
    },
    counts: {
      affected_assets: comparison.assets.length,
      components: rows.length,
      by_disposition: fill(byDisp),
      by_kind: Object.fromEntries(
        Object.keys(byKindDisp)
          .sort(compareCodePoints)
          .map((k) => [k, fill(byKindDisp[k])]),
      ),
      unresolved_components: unresolved.length,
      output_features: ordered.length,
      baseline_features: snapshot.length,
    },
    unresolved,
    notes: [
      ...omittedPendingAdditions.map((aid) => `Pending or flagged addition ${aid} is omitted: additions are inserted only when accepted.`),
      ...(unresolved.length || byDisp.rejected ? ['Rejected, pending and flagged components keep the baseline value and position.'] : []),
    ],
    decisions: rows,
    history: opts.history ?? [],
  };
  return { geojson: { type: 'FeatureCollection', features: ordered }, report };
}
