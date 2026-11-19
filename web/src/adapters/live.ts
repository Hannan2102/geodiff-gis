import type {
  AssetDetail,
  BulkScope,
  ComparisonInfo,
  DatasetSummary,
  DecisionInput,
  DecisionResult,
  Disposition,
  ExportResult,
  FieldSuggestion,
  HistoryEvent,
  MapAsset,
  PublicSource,
  QueueFilter,
  QueueItem,
} from '../types';
import type { ReviewAdapter } from './types';

export const API_URL: string = ((import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000').replace(/\/$/, '');

/** Error with the API's machine-readable code and human guidance. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public guidance = '',
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
  get offline(): boolean {
    return this.status === 0;
  }
}

const TOKEN_KEY = 'geodiff.live.token';
const RUN_KEY = 'geodiff.live.comparison';

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export const liveSession = {
  token: (): string | null => storage()?.getItem(TOKEN_KEY) ?? null,
  comparisonId: (): string | null => storage()?.getItem(RUN_KEY) ?? null,
  save(token: string | null, comparisonId?: string | null) {
    const s = storage();
    if (!s) return;
    if (token) s.setItem(TOKEN_KEY, token);
    else s.removeItem(TOKEN_KEY);
    if (comparisonId !== undefined) {
      if (comparisonId) s.setItem(RUN_KEY, comparisonId);
      else s.removeItem(RUN_KEY);
    }
  },
};

export async function request<T>(
  path: string,
  init: RequestInit & { token?: string | null; timeoutMs?: number } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.token) headers.set('Authorization', `Bearer ${init.token}`);
  if (init.body && typeof init.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  let res: Response;
  try {
    res = await fetch(API_URL + path, { ...init, headers, signal: init.signal ?? AbortSignal.timeout(init.timeoutMs ?? 30_000) });
  } catch (err) {
    const timeout = err instanceof DOMException && err.name === 'TimeoutError';
    throw new ApiError(
      0,
      timeout ? 'timeout' : 'offline',
      timeout ? 'The live API did not answer in time.' : 'The live API could not be reached.',
      timeout
        ? 'A free server may be waking up (this can take up to a minute). Retry shortly.'
        : 'Check your connection, or open the guided sample, which works without the API.',
    );
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let body: unknown;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    const e = (body as { error?: { code: string; message: string; guidance?: string } } | null)?.error;
    throw new ApiError(res.status, e?.code ?? 'http_error', e?.message ?? `HTTP ${res.status}`, e?.guidance ?? '', (e ?? {}) as Record<string, unknown>);
  }
  return body as T;
}

export const workspaceApi = {
  async create(): Promise<string> {
    const r = await request<{ token: string }>('/v1/workspaces', { method: 'POST' });
    return r.token;
  },
  upload(token: string, file: Blob, name: string): Promise<DatasetSummary> {
    return request<DatasetSummary>(`/v1/datasets?name=${encodeURIComponent(name)}`, {
      method: 'POST',
      token,
      body: file,
      headers: { 'Content-Type': 'application/geo+json' },
      timeoutMs: 90_000,
    });
  },
  async sources(): Promise<PublicSource[]> {
    const r = await request<{ enabled: boolean; sources: PublicSource[] }>('/v1/sources', { timeoutMs: 60_000 });
    return r.enabled ? r.sources : [];
  },
  importArcgis(token: string, sourceId: string): Promise<DatasetSummary> {
    return request<DatasetSummary>('/v1/datasets/arcgis', { method: 'POST', token, body: JSON.stringify({ source_id: sourceId }), timeoutMs: 120_000 });
  },
  suggestFields(token: string, baseline: string, proposed: string, bid: string, pid: string): Promise<FieldSuggestion> {
    const q = new URLSearchParams({ baseline, proposed, baseline_id_field: bid, proposed_id_field: pid });
    return request<FieldSuggestion>(`/v1/datasets/suggest-fields?${q}`, { token });
  },
  compare(
    token: string,
    body: {
      baseline_dataset_id: string;
      proposed_dataset_id: string;
      baseline_id_field: string;
      proposed_id_field: string;
      compared_fields: string[];
      tolerance_m: number;
      parent_comparison_id?: string | null;
    },
  ): Promise<ComparisonInfo> {
    return request<ComparisonInfo>('/v1/comparisons', { method: 'POST', token, body: JSON.stringify(body), timeoutMs: 120_000 });
  },
  reset(token: string): Promise<unknown> {
    return request('/v1/workspace/reset', { method: 'POST', token });
  },
  remove(token: string): Promise<unknown> {
    return request('/v1/workspace', { method: 'DELETE', token });
  },
};

export class LiveAdapter implements ReviewAdapter {
  readonly mode = 'live' as const;
  constructor(
    private token: string,
    public comparisonId: string,
  ) {}

  private get<T>(path: string): Promise<T> {
    return request<T>(`/v1/comparisons/${this.comparisonId}${path}`, { token: this.token });
  }
  private post<T>(path: string, body: unknown): Promise<T> {
    return request<T>(`/v1/comparisons/${this.comparisonId}${path}`, { method: 'POST', token: this.token, body: JSON.stringify(body) });
  }

  comparison(): Promise<ComparisonInfo> {
    return this.get('');
  }

  async queue(filter: QueueFilter, onPage?: (items: QueueItem[], total: number) => void): Promise<QueueItem[]> {
    const all: QueueItem[] = [];
    let cursor: string | null = null;
    do {
      const q = new URLSearchParams({ limit: '200' });
      if (filter.kind) q.set('kind', filter.kind);
      if (filter.disposition) q.set('disposition', filter.disposition);
      if (filter.q) q.set('q', filter.q);
      if (cursor) q.set('cursor', cursor);
      const page: { items: QueueItem[]; total_assets: number; next_cursor: string | null } = await this.get(`/queue?${q}`);
      all.push(...page.items);
      onPage?.(all.slice(), page.total_assets);
      cursor = page.next_cursor;
    } while (cursor);
    return all;
  }

  asset(assetId: string): Promise<AssetDetail> {
    return this.get(`/assets/${encodeURIComponent(assetId)}`);
  }

  async mapAssets(bbox: [number, number, number, number]) {
    const fc: { features: { properties: MapAsset }[]; truncated: boolean } = await this.get(`/map?bbox=${bbox.join(',')}&limit=2000`);
    return { assets: fc.features.map((f) => f.properties), truncated: fc.truncated };
  }

  async baselinePoints(bbox: [number, number, number, number]) {
    const fc: { features: { geometry: { coordinates: [number, number] } }[] } = await this.get(`/baseline?bbox=${bbox.join(',')}&limit=2000`);
    return fc.features.map((f) => f.geometry.coordinates);
  }

  decide(items: DecisionInput[]): Promise<DecisionResult> {
    return this.post('/decisions', { decisions: items });
  }

  async bulkPreview(scope: BulkScope): Promise<number> {
    const r: { count: number } = await this.post('/bulk/preview', scope);
    return r.count;
  }

  bulk(scope: BulkScope, disposition: Disposition, note: string | null, expectedCount: number): Promise<DecisionResult> {
    return this.post('/bulk', { scope, disposition, note, expected_count: expectedCount });
  }

  undo(expectedRevision: number) {
    return this.post<DecisionResult & { components: string[] }>('/undo', { expected_revision: expectedRevision });
  }

  async history(): Promise<HistoryEvent[]> {
    const r: { events: HistoryEvent[] } = await this.get('/history?limit=200');
    return r.events;
  }

  exportResult(mode: 'draft' | 'final'): Promise<ExportResult> {
    return this.get(`/export?mode=${mode}&part=bundle`);
  }
}
