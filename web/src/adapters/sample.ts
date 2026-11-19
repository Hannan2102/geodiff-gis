// Guided sample: the engine's precomputed comparison, reviewed entirely in this browser.
// Mirrors the live API's semantics (versions, conflicts, bulk counts, undo by revision) so
// the studio behaves the same in both modes. Decisions persist in localStorage when available.
import { checkDecision, compareCodePoints, FinalExportBlocked, InvalidDecision, resolveExport, type DecisionRecord, type EngineAsset, type EngineComparison, type SnapshotRow } from '../lib/resolve';
import type {
  AssetDetail,
  BulkScope,
  Component,
  ComparisonInfo,
  DecisionInput,
  DecisionResult,
  Disposition,
  ExportResult,
  HistoryEvent,
  LngLat,
  MapAsset,
  Progress,
  QueueFilter,
  QueueItem,
} from '../types';
import { ApiError } from './live';
import type { ReviewAdapter } from './types';

export interface SampleBundle {
  schema: 'geodiff.sample/1';
  title: string;
  synthetic: boolean;
  engine_version: string;
  attribution: Record<string, unknown>;
  focus_asset: string | null;
  default_tolerance_m: number;
  baseline_snapshot: SnapshotRow[];
  presets: { tolerance_m: number; comparison: EngineComparison }[];
  bundle_sha256: string;
}

interface StoredDecision {
  disposition: Disposition;
  note: string | null;
  decided_at: string | null;
  version: number;
}

interface StoredEvent {
  id: number;
  batch_id: string;
  action: 'decide' | 'bulk' | 'undo';
  component_id: string;
  from: Disposition;
  to: Disposition;
  from_note: string | null;
  note: string | null;
  revision: number;
  undone_by: string | null;
  at: string;
}

interface SampleState {
  revision: number;
  nextEventId: number;
  decisions: Record<string, StoredDecision>;
  events: StoredEvent[];
}

const STORAGE_PREFIX = 'geodiff.sample/1';
const memory = new Map<string, string>();

function storageGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return memory.get(key) ?? null;
  }
}
function storageSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    memory.set(key, value);
  }
}
function storageRemovePrefix(prefix: string): void {
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k?.startsWith(prefix)) localStorage.removeItem(k);
    }
  } catch {
    /* storage unavailable */
  }
  for (const k of [...memory.keys()]) if (k.startsWith(prefix)) memory.delete(k);
}

const nowIso = () => new Date().toISOString();
// randomUUID needs a secure context; fall back for plain-HTTP previews.
const newBatch = () => ('randomUUID' in crypto ? crypto.randomUUID() : `b-${Date.now()}-${Math.random().toString(36).slice(2)}`);

let bundlePromise: Promise<SampleBundle> | null = null;

/** Fetches the bundled sample once per page load. */
export function loadSampleBundle(url = `${import.meta.env.BASE_URL}data/demo/sample.json`): Promise<SampleBundle> {
  bundlePromise ??= fetch(url)
    .then((r) => {
      if (!r.ok) throw new ApiError(r.status, 'sample_missing', `The guided sample could not be loaded (HTTP ${r.status}).`, 'Reload the page.');
      return r.json() as Promise<SampleBundle>;
    })
    .catch((e: unknown) => {
      bundlePromise = null;
      throw e instanceof ApiError ? e : new ApiError(0, 'sample_unavailable', 'The guided sample could not be loaded.', 'Check your connection and reload the page.');
    });
  return bundlePromise;
}

export function samplePrefix(bundle: Pick<SampleBundle, 'bundle_sha256'>): string {
  return `${STORAGE_PREFIX}/${bundle.bundle_sha256.slice(0, 16)}`;
}

/** Clears every preset's decisions for this bundle (the "Reset sample" action). */
export function resetSample(bundle: Pick<SampleBundle, 'bundle_sha256'>): void {
  storageRemovePrefix(samplePrefix(bundle));
}

export class SampleAdapter implements ReviewAdapter {
  readonly mode = 'sample' as const;
  readonly comparisonData: EngineComparison;
  private readonly assets: Map<string, EngineAsset>;
  private readonly ids: string[];
  private readonly key: string;
  private state: SampleState;

  constructor(
    readonly bundle: SampleBundle,
    readonly toleranceM: number,
  ) {
    const run = bundle.presets.find((p) => p.tolerance_m === toleranceM) ?? bundle.presets.find((p) => p.tolerance_m === bundle.default_tolerance_m)!;
    this.comparisonData = run.comparison;
    this.assets = new Map(run.comparison.assets.map((a) => [a.asset_id, a]));
    this.ids = [...this.assets.keys()].sort(compareCodePoints);
    this.key = `${samplePrefix(bundle)}/${run.tolerance_m}`;
    this.state = this.load();
  }

  get focusAsset(): string | null {
    return this.bundle.focus_asset;
  }

  get comparisonId(): string {
    return `sample-${this.comparisonData.parameters.tolerance_m}m`;
  }

  private load(): SampleState {
    const fresh: SampleState = { revision: 0, nextEventId: 1, decisions: {}, events: [] };
    const raw = storageGet(this.key);
    if (!raw) return fresh;
    try {
      const s = JSON.parse(raw) as SampleState;
      // Drop anything that no longer matches this bundle's components.
      for (const cid of Object.keys(s.decisions)) if (!this.componentById(cid)) delete s.decisions[cid];
      return { ...fresh, ...s };
    } catch {
      return fresh;
    }
  }

  private save(): void {
    storageSet(this.key, JSON.stringify(this.state));
  }

  private componentById(cid: string) {
    const aid = cid.slice(0, cid.indexOf(':'));
    return this.assets.get(aid)?.components.find((c) => c.id === cid) ?? null;
  }

  private decision(cid: string): StoredDecision {
    return this.state.decisions[cid] ?? { disposition: 'pending', note: null, decided_at: null, version: 0 };
  }

  private component(c: EngineAsset['components'][number]): Component {
    const d = this.decision(c.id);
    return { component_id: c.id, kind: c.kind, field: c.field ?? null, evidence: c.evidence, patch: c.patch, decision: { ...d } };
  }

  private progress(): Progress {
    const by: Record<Disposition, number> = { pending: 0, accepted: 0, rejected: 0, flagged: 0 };
    let resolvedAssets = 0;
    for (const a of this.assets.values()) {
      let open = 0;
      for (const c of a.components) {
        const d = this.decision(c.id).disposition;
        by[d]++;
        if (d === 'pending' || d === 'flagged') open++;
      }
      if (open === 0) resolvedAssets++;
    }
    const total = by.pending + by.accepted + by.rejected + by.flagged;
    const resolved = by.accepted + by.rejected;
    return {
      components: total,
      by_disposition: by,
      resolved_components: resolved,
      unresolved_components: total - resolved,
      affected_assets: this.assets.size,
      resolved_assets: resolvedAssets,
      complete: total === resolved,
    };
  }

  private extent(): [number, number, number, number] | null {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const add = (c: LngLat | null | undefined) => {
      if (!c) return;
      x0 = Math.min(x0, c[0]);
      y0 = Math.min(y0, c[1]);
      x1 = Math.max(x1, c[0]);
      y1 = Math.max(y1, c[1]);
    };
    for (const a of this.assets.values()) {
      add(a.baseline?.coordinates);
      add(a.proposed?.coordinates);
    }
    if (x0 === Infinity) for (const r of this.bundle.baseline_snapshot) add(r.feature.geometry.coordinates);
    return x0 === Infinity ? null : [x0, y0, x1, y1];
  }

  async comparison(): Promise<ComparisonInfo> {
    const c = this.comparisonData;
    const s = c.summary as unknown as ComparisonInfo['summary'];
    return {
      comparison_id: this.comparisonId,
      parent_comparison_id: null,
      revision: this.state.revision,
      created_at: c.created_at,
      parameters: {
        tolerance_m: c.parameters.tolerance_m,
        baseline_id_field: c.parameters.baseline_id_field,
        proposed_id_field: c.parameters.proposed_id_field,
        compared_fields: c.parameters.compared_fields,
        distance_method: c.parameters.distance_method ?? '',
        units: c.parameters.units ?? 'meters',
      },
      inputs: { baseline: { ...c.inputs.baseline, source: this.bundle.attribution }, proposed: { ...c.inputs.proposed, source: this.bundle.attribution } },
      summary: s,
      warnings: c.warnings,
      progress: this.progress(),
      extent: this.extent(),
    };
  }

  private matches(a: EngineAsset, f: QueueFilter): boolean {
    if (f.q && !a.asset_id.toLowerCase().includes(f.q.toLowerCase())) return false;
    return a.components.some((c) => {
      if (f.kind && c.kind !== f.kind) return false;
      const d = this.decision(c.id).disposition;
      if (f.disposition === 'unresolved') return d === 'pending' || d === 'flagged';
      return !f.disposition || d === f.disposition;
    });
  }

  async queue(filter: QueueFilter, onPage?: (items: QueueItem[], total: number) => void): Promise<QueueItem[]> {
    const items: QueueItem[] = [];
    for (const id of this.ids) {
      const a = this.assets.get(id)!;
      if (!this.matches(a, filter)) continue;
      items.push({ asset_id: id, status: a.status, location: (a.proposed ?? a.baseline)!.coordinates, components: a.components.map((c) => this.component(c)) });
    }
    onPage?.(items.slice(), items.length);
    return items;
  }

  async asset(assetId: string): Promise<AssetDetail> {
    const a = this.assets.get(assetId);
    if (!a) throw new ApiError(404, 'not_found', `Asset ${assetId} is not part of this review.`, 'Pick an asset from the queue.');
    const prefix = `${assetId}:`;
    const history = this.state.events
      .filter((e) => e.component_id.startsWith(prefix))
      .reverse()
      .slice(0, 100)
      .map((e) => this.eventOut(e));
    return {
      asset_id: assetId,
      status: a.status,
      revision: this.state.revision,
      baseline: a.baseline,
      proposed: a.proposed,
      components: a.components.map((c) => this.component(c)),
      history,
    };
  }

  async mapAssets(bbox: [number, number, number, number]) {
    const inside = (c: LngLat | null | undefined) => !!c && c[0] >= bbox[0] && c[0] <= bbox[2] && c[1] >= bbox[1] && c[1] <= bbox[3];
    const assets: MapAsset[] = [];
    for (const id of this.ids) {
      const a = this.assets.get(id)!;
      if (!inside(a.baseline?.coordinates) && !inside(a.proposed?.coordinates)) continue;
      const distances = a.components.map((c) => c.evidence.distance_m).filter((d): d is number => typeof d === 'number');
      assets.push({
        asset_id: id,
        status: a.status,
        kinds: a.components.map((c) => c.kind),
        dispositions: a.components.map((c) => this.decision(c.id).disposition),
        from: a.baseline?.coordinates ?? null,
        to: a.proposed?.coordinates ?? null,
        distance_m: distances.length ? Math.max(...distances) : null,
      });
    }
    return { assets, truncated: false };
  }

  async baselinePoints(bbox: [number, number, number, number]): Promise<[number, number][]> {
    const out: [number, number][] = [];
    for (const r of this.bundle.baseline_snapshot) {
      const [x, y] = r.feature.geometry.coordinates;
      if (x >= bbox[0] && x <= bbox[2] && y >= bbox[1] && y <= bbox[3]) out.push([x, y]);
      if (out.length >= 2000) break;
    }
    return out;
  }

  private apply(action: StoredEvent['action'], changes: { cid: string; disposition: Disposition; note: string | null; expected: number | null }[]): { revision: number; batch: string } {
    const conflicts = [];
    for (const ch of changes) {
      if (!this.componentById(ch.cid)) throw new ApiError(404, 'not_found', `Component ${ch.cid} was not found.`, 'Reload the review.');
      const cur = this.decision(ch.cid);
      if (ch.expected !== null && cur.version !== ch.expected) conflicts.push({ component_id: ch.cid, expected_version: ch.expected, current_version: cur.version });
    }
    if (conflicts.length) {
      throw new ApiError(409, 'stale_decision', `${conflicts.length} component(s) changed since this view was loaded.`, 'Another tab updated these decisions. Reload the asset and decide again.', { conflicts });
    }
    const revision = this.state.revision + 1;
    const batch = newBatch();
    const at = nowIso();
    for (const ch of changes) {
      const cur = this.decision(ch.cid);
      this.state.decisions[ch.cid] = { disposition: ch.disposition, note: ch.note, decided_at: ch.disposition === 'pending' ? null : at, version: cur.version + 1 };
      this.state.events.push({ id: this.state.nextEventId++, batch_id: batch, action, component_id: ch.cid, from: cur.disposition, to: ch.disposition, from_note: cur.note, note: ch.note, revision, undone_by: null, at });
    }
    this.state.revision = revision;
    this.save();
    return { revision, batch };
  }

  private validate(disposition: Disposition, note: string | null | undefined) {
    try {
      checkDecision({ disposition, note });
    } catch (e) {
      if (e instanceof InvalidDecision && disposition === 'flagged') {
        throw new ApiError(422, 'note_required', 'Flagging requires a note.', "Say what needs follow-up, for example 'confirm location with crew'.");
      }
      throw new ApiError(422, 'invalid_disposition', e instanceof Error ? e.message : String(e), 'Use pending, accepted, rejected or flagged.');
    }
  }

  async decide(items: DecisionInput[]): Promise<DecisionResult> {
    this.sync();
    for (const it of items) this.validate(it.disposition, it.note);
    const { revision } = this.apply(
      'decide',
      items.map((it) => ({ cid: it.component_id, disposition: it.disposition, note: it.note?.trim() ? it.note.trim() : null, expected: it.expected_version })),
    );
    return { revision, progress: this.progress() };
  }

  private targets(scope: BulkScope): string[] {
    const out: string[] = [];
    for (const id of this.ids) {
      if (scope.asset_ids && scope.asset_ids.length && !scope.asset_ids.includes(id)) continue;
      for (const c of this.assets.get(id)!.components) {
        if (scope.kind && c.kind !== scope.kind) continue;
        if (scope.field && c.field !== scope.field) continue;
        if (scope.current_disposition && this.decision(c.id).disposition !== scope.current_disposition) continue;
        out.push(c.id);
      }
    }
    return out;
  }

  async bulkPreview(scope: BulkScope): Promise<number> {
    this.sync();
    return this.targets(scope).length;
  }

  async bulk(scope: BulkScope, disposition: Disposition, note: string | null, expectedCount: number): Promise<DecisionResult> {
    this.sync();
    this.validate(disposition, note);
    if (!scope.kind && !scope.field && !scope.current_disposition && !scope.asset_ids?.length) {
      throw new ApiError(422, 'scope_required', 'A bulk decision needs an explicit scope.', 'Filter by kind, field, current disposition or asset IDs.');
    }
    const t = this.targets(scope);
    if (t.length !== expectedCount) {
      throw new ApiError(409, 'bulk_count_mismatch', `The scope now matches ${t.length} components, not ${expectedCount}.`, 'Review the updated count and confirm again.', { actual_count: t.length });
    }
    const { revision } = this.apply('bulk', t.map((cid) => ({ cid, disposition, note, expected: null })));
    return { revision, progress: this.progress() };
  }

  async undo(expectedRevision: number): Promise<DecisionResult & { components: string[] }> {
    this.sync();
    if (this.state.revision !== expectedRevision) {
      throw new ApiError(409, 'stale_revision', `The review is at revision ${this.state.revision}, not ${expectedRevision}.`, 'Reload to see the latest decisions before undoing.', { current_revision: this.state.revision });
    }
    const last = [...this.state.events].reverse().find((e) => e.action !== 'undo' && e.undone_by === null);
    if (!last) throw new ApiError(409, 'nothing_to_undo', 'There is no decision left to undo.', '');
    const events = this.state.events.filter((e) => e.batch_id === last.batch_id);
    const { revision, batch } = this.apply('undo', events.map((e) => ({ cid: e.component_id, disposition: e.from, note: e.from_note, expected: null })));
    for (const e of events) e.undone_by = batch;
    this.save();
    return { revision, progress: this.progress(), components: events.map((e) => e.component_id) };
  }

  private eventOut(e: StoredEvent): HistoryEvent {
    return { batch_id: e.batch_id, action: e.action, component_id: e.component_id, from: e.from, to: e.to, note: e.note, revision: e.revision, undone: e.undone_by !== null, at: e.at };
  }

  async history(): Promise<HistoryEvent[]> {
    return [...this.state.events].reverse().slice(0, 200).map((e) => this.eventOut(e));
  }

  async exportResult(mode: 'draft' | 'final'): Promise<ExportResult> {
    this.sync();
    const decisions: Record<string, DecisionRecord> = {};
    for (const [cid, d] of Object.entries(this.state.decisions)) decisions[cid] = { disposition: d.disposition, note: d.note, decided_at: d.decided_at };
    try {
      return resolveExport(this.bundle.baseline_snapshot, this.comparisonData, decisions, {
        mode,
        attribution: this.bundle.attribution,
        history: [...this.state.events].reverse().map((e) => this.eventOut(e)),
      });
    } catch (e) {
      if (e instanceof FinalExportBlocked) {
        throw new ApiError(409, 'unresolved_components', e.message, 'Accept or reject every pending and flagged component, or download a draft export.');
      }
      throw e;
    }
  }

  /** Pick up decisions made in another tab before writing (localStorage is shared). */
  private sync(): void {
    this.state = this.load();
  }
}
