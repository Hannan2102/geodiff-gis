import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { SampleBundle } from '../adapters/sample';
import { compareCodePoints, FinalExportBlocked, resolveExport, type DecisionRecord } from './resolve';

const fixtures = join(__dirname, '..', '..', '..', 'fixtures');
const read = <T>(...p: string[]) => JSON.parse(readFileSync(join(fixtures, ...p), 'utf8')) as T;

describe('browser resolver parity with the Python engine', () => {
  const bundle = read<SampleBundle>('demo', 'sample.json');
  const parity = read<{ bundle_sha256: string; cases: { tolerance_m: number; decisions: Record<string, DecisionRecord>; geojson: unknown; report: Record<string, unknown> }[] }>('demo', 'parity', 'sample_parity.json');

  it('parity fixture was built from this bundle', () => {
    expect(parity.bundle_sha256).toBe(bundle.bundle_sha256);
    expect(parity.cases.map((c) => c.tolerance_m)).toEqual(bundle.presets.map((p) => p.tolerance_m));
  });

  for (const c of parity.cases) {
    it(`matches the Python draft export and report at ${c.tolerance_m} m`, () => {
      const run = bundle.presets.find((p) => p.tolerance_m === c.tolerance_m)!;
      const exportedAt = c.report.exported_at as string;
      const out = resolveExport(bundle.baseline_snapshot, run.comparison, c.decisions, { mode: 'draft', exportedAt, attribution: bundle.attribution });
      expect(out.geojson).toEqual(c.geojson);
      expect(out.report).toEqual(c.report);
    });
  }

  it('reproduces the hand-authored golden export from the golden decisions', () => {
    const golden = read<SampleBundle>('demo', 'parity', 'golden_bundle.json');
    const decisions = read<Record<string, DecisionRecord>>('golden', 'decisions.json');
    const expected = read<{ features: unknown[] }>('golden', 'expected_reviewed.geojson');
    const out = resolveExport(golden.baseline_snapshot, golden.presets[0]!.comparison, decisions, { mode: 'final' });
    expect(out.geojson.features).toEqual(expected.features);
    expect(out.report.complete).toBe(true);
  });

  it('blocks a final export while anything is pending', () => {
    const run = bundle.presets[0]!;
    expect(() => resolveExport(bundle.baseline_snapshot, run.comparison, {}, { mode: 'final' })).toThrow(FinalExportBlocked);
  });

  it('sorts IDs by code point like Python', () => {
    const ids = ['b', 'B', 'a', '\u{1F600}', 'Ａ', 'A-10', 'A-2'];
    expect([...ids].sort(compareCodePoints)).toEqual(['A-10', 'A-2', 'B', 'a', 'b', 'Ａ', '\u{1F600}']);
  });
});
