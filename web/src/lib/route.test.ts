import { describe, expect, it } from 'vitest';
import { formatRoute, parseRoute } from './route';

describe('hash routes', () => {
  it('round-trips sample and live routes', () => {
    for (const h of ['#/sample/BENCH-0142?t=2', '#/sample?t=5', '#/live/SIGN-0050', '#/sample/A%2FB%20C?t=1']) {
      expect(formatRoute(parseRoute(h))).toBe(h);
    }
    expect(parseRoute('#/sample/A%2FB%20C?t=1').asset).toBe('A/B C');
  });
  it('ignores unknown or malformed hashes', () => {
    expect(parseRoute('').mode).toBeNull();
    expect(parseRoute('#/other/x').mode).toBeNull();
    expect(parseRoute('#/sample/%E0%A4%A?t=x')).toEqual({ mode: 'sample', asset: null, tolerance: null });
  });
});
