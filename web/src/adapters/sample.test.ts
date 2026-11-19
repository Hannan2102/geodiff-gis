import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';
import { ApiError } from './live';
import { resetSample, SampleAdapter, type SampleBundle } from './sample';

const bundle = JSON.parse(readFileSync(join(__dirname, '..', '..', '..', 'fixtures', 'demo', 'sample.json'), 'utf8')) as SampleBundle;

describe('SampleAdapter', () => {
  beforeEach(() => resetSample(bundle));

  it('opens the BENCH-0142 story at the default 2 m preset', async () => {
    const a = new SampleAdapter(bundle, 2);
    const info = await a.comparison();
    expect(info.parameters.tolerance_m).toBe(2);
    expect(info.progress.complete).toBe(false);
    const d = await a.asset('BENCH-0142');
    expect(d.components.map((c) => c.component_id)).toEqual(['BENCH-0142:move', 'BENCH-0142:attr:condition']);
    expect(d.components[0]!.evidence.distance_m).toBeCloseTo(119.6067, 3);
  });

  it('accept condition, reject move: export keeps the approved location with the new condition', async () => {
    const a = new SampleAdapter(bundle, 2);
    await a.decide([
      { component_id: 'BENCH-0142:attr:condition', disposition: 'accepted', expected_version: 0 },
      { component_id: 'BENCH-0142:move', disposition: 'rejected', expected_version: 0 },
    ]);
    const { geojson } = await a.exportResult('draft');
    const bench = geojson.features.find((f) => f.properties.asset_id === 'BENCH-0142')!;
    expect(bench.properties.condition).toBe('damaged');
    expect(bench.geometry.coordinates).toEqual([-96.7969, 32.78434]);
  });

  it('persists across adapter instances (page refresh) and resets', async () => {
    await new SampleAdapter(bundle, 2).decide([{ component_id: 'BENCH-0142:move', disposition: 'rejected', expected_version: 0 }]);
    const again = new SampleAdapter(bundle, 2);
    expect((await again.asset('BENCH-0142')).components[0]!.decision.disposition).toBe('rejected');
    expect((await new SampleAdapter(bundle, 5).comparison()).progress.resolved_components).toBe(0);
    resetSample(bundle);
    expect((await new SampleAdapter(bundle, 2).asset('BENCH-0142')).components[0]!.decision.disposition).toBe('pending');
  });

  it('rejects stale versions, notes-less flags and mismatched bulk counts like the API', async () => {
    const a = new SampleAdapter(bundle, 2);
    await a.decide([{ component_id: 'BENCH-0142:move', disposition: 'rejected', expected_version: 0 }]);
    await expect(a.decide([{ component_id: 'BENCH-0142:move', disposition: 'accepted', expected_version: 0 }])).rejects.toMatchObject({ status: 409, code: 'stale_decision' });
    await expect(a.decide([{ component_id: 'BENCH-0142:attr:condition', disposition: 'flagged', expected_version: 0 }])).rejects.toMatchObject({ code: 'note_required' });
    const n = await a.bulkPreview({ kind: 'addition' });
    await expect(a.bulk({ kind: 'addition' }, 'accepted', null, n + 1)).rejects.toBeInstanceOf(ApiError);
    await a.bulk({ kind: 'addition' }, 'accepted', null, n);
    expect((await a.comparison()).progress.by_disposition.accepted).toBe(n);
  });

  it('undo reverts the last batch only and requires the current revision', async () => {
    const a = new SampleAdapter(bundle, 2);
    await a.decide([{ component_id: 'BENCH-0142:move', disposition: 'rejected', expected_version: 0 }]);
    const r = await a.decide([{ component_id: 'BENCH-0142:attr:condition', disposition: 'accepted', expected_version: 0 }]);
    await expect(a.undo(r.revision - 1)).rejects.toMatchObject({ code: 'stale_revision' });
    const u = await a.undo(r.revision);
    expect(u.components).toEqual(['BENCH-0142:attr:condition']);
    const d = await a.asset('BENCH-0142');
    expect(d.components.map((c) => c.decision.disposition)).toEqual(['rejected', 'pending']);
  });

  it('final export unlocks only when every component is decided', async () => {
    const a = new SampleAdapter(bundle, 5);
    await expect(a.exportResult('final')).rejects.toMatchObject({ code: 'unresolved_components' });
    const n = await a.bulkPreview({ current_disposition: 'pending' });
    await a.bulk({ current_disposition: 'pending' }, 'rejected', null, n);
    const { geojson, report } = await a.exportResult('final');
    expect(report.complete).toBe(true);
    // Everything rejected: the export is exactly the approved baseline.
    expect(geojson.features).toEqual(bundle.baseline_snapshot.map((r) => r.feature));
  });
});
