import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchReadiness } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('fetchReadiness', () => {
  it('reports an unreachable API without throwing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const r = await fetchReadiness();
    expect(r.kind).toBe('unreachable');
  });

  it('distinguishes a running API whose database is down', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'unavailable', database: false }), { status: 503 })),
    );
    const r = await fetchReadiness();
    expect(r.kind).toBe('degraded');
  });
});
